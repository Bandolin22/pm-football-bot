from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from pm_football_bot.board import UpcomingMatch, list_upcoming
from pm_football_bot.config import ROOT, hydrate_env, load_settings
from pm_football_bot.gamma import GammaClient
from pm_football_bot.models import utcnow

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
LEAD_MINUTES = 60
POLL_SECONDS = 60
SENT_KEEP_DAYS = 7


def sent_path() -> Path:
    override = (os.environ.get("NOTIFY_STATE_PATH") or "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "notify" / "sent.json"


def telegram_creds() -> tuple[str, str] | None:
    hydrate_env()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def minutes_to_kickoff(match: UpcomingMatch, now: datetime) -> float | None:
    if match.kickoff is None:
        return None
    kick = match.kickoff
    if kick.tzinfo is None:
        kick = kick.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (kick - now).total_seconds() / 60.0


def is_pre_kick_alert(match: UpcomingMatch, now: datetime, lead_minutes: int = LEAD_MINUTES) -> bool:
    """True in the hour before kickoff (still pre-match)."""
    if not match.watch:
        return False
    mins = minutes_to_kickoff(match, now)
    if mins is None:
        return False
    return 0 <= mins <= lead_minutes


def due_alerts(
    matches: list[UpcomingMatch],
    now: datetime,
    sent: set[str],
    lead_minutes: int = LEAD_MINUTES,
) -> list[UpcomingMatch]:
    due: list[UpcomingMatch] = []
    for match in matches:
        if not match.slug or match.slug in sent:
            continue
        if is_pre_kick_alert(match, now, lead_minutes):
            due.append(match)
    due.sort(key=lambda row: (row.kickoff or datetime.max.replace(tzinfo=timezone.utc), row.title))
    return due


def format_alert(match: UpcomingMatch, now: datetime) -> str:
    mins = minutes_to_kickoff(match, now)
    if mins is None:
        eta = "kickoff time unknown"
    elif mins >= 1:
        eta = f"kickoff in {mins:.0f}m"
    else:
        eta = "kickoff now"
    kick = "unknown"
    if match.kickoff is not None:
        kick = match.kickoff.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
    return (
        f"★ {match.league_name} · {eta}\n"
        f"{match.title}\n"
        f"{_pct(match.home_pct)} Home · {_pct(match.draw_pct)} Draw · {_pct(match.away_pct)} Away\n"
        f"{kick}\n"
        f"{match.url}"
    )


def send_telegram(token: str, chat_id: str, text: str, session: requests.Session | None = None) -> None:
    client = session or requests.Session()
    response = client.post(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=30,
    )
    response.raise_for_status()


def load_sent(path: Path | None = None) -> dict[str, str]:
    path = path or sent_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def save_sent(rows: dict[str, str], path: Path | None = None, now: datetime | None = None) -> None:
    path = path or sent_path()
    now = now or utcnow()
    cutoff = now - timedelta(days=SENT_KEEP_DAYS)
    kept: dict[str, str] = {}
    for slug, stamp in rows.items():
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            kept[slug] = when.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, indent=2), encoding="utf-8")


def mark_sent(rows: dict[str, str], slug: str, now: datetime | None = None) -> dict[str, str]:
    now = now or utcnow()
    updated = dict(rows)
    updated[slug] = now.isoformat()
    return updated


def run_once(
    *,
    dry_run: bool = False,
    lead_minutes: int = LEAD_MINUTES,
    now: datetime | None = None,
    session: requests.Session | None = None,
) -> int:
    now = now or utcnow()
    settings = load_settings()
    client = GammaClient(settings, session=session)
    matches = list_upcoming(
        client,
        settings.leagues,
        now=now,
        per_league=None,
        include_disabled=True,
    )
    path = sent_path()
    sent = load_sent(path)
    due = due_alerts(matches, now, set(sent), lead_minutes=lead_minutes)
    if not due:
        print("No watchlist kickoffs in the next hour.")
        return 0

    creds = None if dry_run else telegram_creds()
    if not dry_run and creds is None:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 2

    for match in due:
        text = format_alert(match, now)
        print(text)
        print()
        if dry_run:
            continue
        send_telegram(creds[0], creds[1], text, session=session)
        sent = mark_sent(sent, match.slug, now)
        save_sent(sent, path, now=now)
    return 0


def loop_forever(*, dry_run: bool = False, lead_minutes: int = LEAD_MINUTES, every: int = POLL_SECONDS) -> int:
    """Poll until missing Telegram creds. One failed check does not stop the process."""
    wait = max(15, every)
    while True:
        try:
            code = run_once(dry_run=dry_run, lead_minutes=lead_minutes)
            if code == 2:
                return code
        except Exception as exc:
            print(f"watch-alert check failed: {exc}")
        time.sleep(wait)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Telegram alert 1 hour before watchlist kickoffs, with live Polymarket 1X2."
    )
    parser.add_argument("--loop", action="store_true", help="Keep polling.")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts, do not send Telegram.")
    parser.add_argument("--lead-minutes", type=int, default=LEAD_MINUTES)
    parser.add_argument("--every", type=int, default=POLL_SECONDS, help="Seconds between loops.")
    args = parser.parse_args(argv)

    if args.loop:
        return loop_forever(dry_run=args.dry_run, lead_minutes=args.lead_minutes, every=args.every)
    return run_once(dry_run=args.dry_run, lead_minutes=args.lead_minutes)


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


if __name__ == "__main__":
    raise SystemExit(main())
