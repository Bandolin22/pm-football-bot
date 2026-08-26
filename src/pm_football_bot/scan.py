from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from typing import Callable

from pm_football_bot.config import Settings, load_settings
from pm_football_bot.execution import LiveTradingDisabled, place_ticket
from pm_football_bot.gamma import GammaClient
from pm_football_bot.models import utcnow
from pm_football_bot.report import ScanResult
from pm_football_bot.risk import size_book
from pm_football_bot.signals import is_mismatch, propose_tickets


ProgressFn = Callable[[str], None]


def collect_scan(
    live: bool = False,
    on_progress: ProgressFn | None = None,
    settings: Settings | None = None,
) -> ScanResult:
    settings = settings or load_settings()
    if live:
        settings = replace(settings, dry_run=False)

    client = GammaClient(settings)
    now = utcnow()
    rows = []
    scanned = 0
    mismatches = 0

    for league in settings.leagues:
        if not league.enabled:
            continue
        if on_progress:
            on_progress(f"Loading {league.name}…")
        events = client.list_moneyline_events(league)
        for event in events:
            scanned += 1
            fixture = client.parse_moneyline(league, event)
            if not is_mismatch(fixture, settings):
                continue
            mismatches += 1
            if on_progress:
                on_progress(f"Mismatch: {fixture.title}")
            fixture = client.attach_more_markets(fixture)
            rows.extend(propose_tickets(fixture, settings, now=now))

    booked = size_book(rows, settings)
    return ScanResult(
        settings=settings,
        scanned=scanned,
        mismatches=mismatches,
        tickets=booked,
        as_of=now,
    )


def scan_once(live: bool = False) -> int:
    result = collect_scan(live=live)
    settings = result.settings
    booked = result.tickets

    print(
        f"bankroll ${settings.bankroll_usd:.0f}  "
        f"ticket ${settings.ticket_usd:.0f}  "
        f"cap ${settings.max_open_usd:.0f}  "
        f"mode {'LIVE' if not settings.dry_run else 'DRY-RUN'}",
        flush=True,
    )
    print(
        f"fixtures {result.scanned}  mismatches {result.mismatches}  "
        f"tickets {len(booked)}  after risk cap {len(booked)}"
    )
    print()

    if not booked:
        print("No liquid mismatch tickets in the current window.")
        print("Even games are ignored on purpose. Widen mismatch.max_dog_yes only if you mean to.")
        return 0

    print(f"{'league':<8} {'rule':<12} {'px':>6} {'sh':>7} {'usd':>7}  fixture")
    for ticket in booked:
        print(
            f"{ticket.league:<8} {ticket.rule_id:<12} "
            f"{ticket.price:6.3f} {ticket.shares:7.2f} {ticket.cost_usd:7.2f}  "
            f"{ticket.fixture} [{ticket.outcome}]"
        )
        print(f"{'':8} {ticket.reason}  spread={ticket.spread}")

    total = sum(t.cost_usd for t in booked)
    print()
    print(f"planned notional ${total:.2f}")

    if settings.dry_run:
        return 0

    errors = 0
    for ticket in booked:
        try:
            placed = place_ticket(ticket, settings)
            print(json.dumps({"placed": ticket.rule_id, "fixture": ticket.fixture, "result": str(placed)[:300]}))
        except LiveTradingDisabled as exc:
            print(f"live trading disabled: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 — surface venue errors, keep scanning
            errors += 1
            print(f"order failed {ticket.fixture} {ticket.rule_id}: {exc}", file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan EPL/LaLiga/Ligue 1/Serie A/Bundesliga mismatch harvest tickets"
    )
    parser.add_argument("--live", action="store_true", help="Place GTC bids (requires .env keys)")
    parser.add_argument("--loop", action="store_true", help="Repeat on poll_seconds")
    args = parser.parse_args(argv)

    if args.loop:
        import time

        settings = load_settings()
        while True:
            code = scan_once(live=args.live)
            if code == 2:
                return code
            time.sleep(settings.poll_seconds)
    return scan_once(live=args.live)


if __name__ == "__main__":
    raise SystemExit(main())
