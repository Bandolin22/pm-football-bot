from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pm_football_bot.board import UpcomingMatch
from pm_football_bot.notify import due_alerts, fetch_horizon_hours, format_alert, is_pre_kick_alert, mark_sent, sent_path


def _match(
    *,
    hours: float,
    watch: bool = True,
    slug: str = "epl-ars-che-2026-08-30",
    title: str = "Arsenal FC vs. Chelsea FC",
) -> UpcomingMatch:
    now = datetime.now(timezone.utc)
    return UpcomingMatch(
        league="epl",
        league_name="Premier League",
        title=title,
        slug=slug,
        kickoff=now + timedelta(hours=hours),
        home_team="Arsenal FC",
        away_team="Chelsea FC",
        home_pct=0.23,
        draw_pct=0.25,
        away_pct=0.52,
        watch=watch,
    )


def test_alerts_in_the_lead_window_before_kickoff():
    now = datetime.now(timezone.utc)
    soon = _match(hours=0.7)
    this_window = _match(hours=2.5, slug="epl-liv-ful")
    later = _match(hours=5, slug="later")
    past = _match(hours=-0.1, slug="past")
    other = _match(hours=0.5, watch=False, slug="other")
    laliga = UpcomingMatch(
        league="laliga",
        league_name="LaLiga",
        title="Real Madrid CF vs. Real Sociedad",
        slug="laliga-rma-rso",
        kickoff=now + timedelta(hours=1.2),
        home_team="Real Madrid CF",
        away_team="Real Sociedad",
        home_pct=0.61,
        draw_pct=0.22,
        away_pct=0.17,
        watch=True,
    )
    assert is_pre_kick_alert(soon, now)
    assert is_pre_kick_alert(this_window, now)
    assert is_pre_kick_alert(laliga, now)
    assert not is_pre_kick_alert(later, now)
    assert not is_pre_kick_alert(past, now)
    assert not is_pre_kick_alert(other, now)
    due = due_alerts([soon, this_window, later, past, other, laliga], now, sent=set())
    assert [row.slug for row in due] == ["epl-ars-che-2026-08-30", "laliga-rma-rso", "epl-liv-ful"]


def test_fetch_horizon_covers_lead_window():
    assert fetch_horizon_hours(180) >= 6


def test_skips_already_sent_slug():
    now = datetime.now(timezone.utc)
    match = _match(hours=0.4)
    due = due_alerts([match], now, sent={match.slug})
    assert due == []


def test_format_includes_polymarket_status_and_link():
    now = datetime.now(timezone.utc)
    text = format_alert(_match(hours=1), now)
    assert "Arsenal FC vs. Chelsea FC" in text
    assert "23% Home" in text
    assert "25% Draw" in text
    assert "52% Away" in text
    assert "https://polymarket.com/event/epl-ars-che-2026-08-30" in text
    assert "Premier League" in text


def test_mark_sent_records_slug():
    now = datetime.now(timezone.utc)
    rows = mark_sent({}, "epl-ars-che-2026-08-30", now)
    assert "epl-ars-che-2026-08-30" in rows


def test_sent_path_honors_env(tmp_path, monkeypatch):
    target = tmp_path / "sent.json"
    monkeypatch.setenv("NOTIFY_STATE_PATH", str(target))
    assert sent_path() == target
