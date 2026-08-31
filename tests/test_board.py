from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pm_football_bot.board import (
    from_fixture,
    involves_watch_club,
    list_upcoming,
    poly_event_url,
    soonest,
    take_upcoming,
)
from pm_football_bot.config import League
from pm_football_bot.gamma import invert_book
from pm_football_bot.models import BinaryMarket, Fixture, OutcomeBook, SideQuote


def _book(token: str, label: str, mid: float) -> OutcomeBook:
    return OutcomeBook(token_id=token, label=label, mid=mid, best_bid=mid - 0.01, best_ask=mid + 0.01)


def _side(team: str, yes_mid: float) -> SideQuote:
    invert_book(yes_mid - 0.01, yes_mid + 0.01)
    return SideQuote(
        team=team,
        yes=_book(f"{team}-yes", "Yes", yes_mid),
        no=_book(f"{team}-no", "No", round(1 - yes_mid, 4)),
    )


def _fixture(
    *,
    league: str = "epl",
    title: str = "Arsenal FC vs. Chelsea FC",
    slug: str = "epl-ars-che-2026-08-30",
    hours: float = 24,
    home: float = 0.52,
    away: float = 0.24,
    draw: float | None = 0.24,
) -> Fixture:
    kickoff = datetime.now(timezone.utc) + timedelta(hours=hours)
    draw_mkt = None
    if draw is not None:
        draw_mkt = BinaryMarket(
            question="Draw",
            slug=f"{slug}-draw",
            kind="moneyline",
            line=None,
            outcomes=(_book("d-yes", "Yes", draw), _book("d-no", "No", round(1 - draw, 4))),
        )
    home_name, away_name = title.split(" vs. ", 1)
    return Fixture(
        league=league,
        title=title,
        slug=slug,
        kickoff=kickoff,
        home=_side(home_name, home),
        away=_side(away_name, away),
        draw=draw_mkt,
    )


def test_poly_event_url():
    assert poly_event_url("epl-ars-che-2026-08-30") == "https://polymarket.com/event/epl-ars-che-2026-08-30"


def test_skips_past_kickoffs_and_keeps_next_twenty():
    now = datetime.now(timezone.utc)
    past = _fixture(hours=-3, slug="past")
    future = [_fixture(hours=i + 1, slug=f"f{i}", title=f"Team A vs. Team B {i}") for i in range(25)]
    picked = take_upcoming([past, *future], now, limit=20)
    assert past not in picked
    assert len(picked) == 20
    assert [row.slug for row in picked] == [f"f{i}" for i in range(20)]
    assert [row.slug for row in take_upcoming([past, *future], now, limit=None)] == [f"f{i}" for i in range(25)]


def test_from_fixture_copies_polymarket_percents():
    row = from_fixture(_fixture(home=0.61, draw=0.22, away=0.17), "Premier League")
    assert row.home_pct == 0.61
    assert row.draw_pct == 0.22
    assert row.away_pct == 0.17
    assert row.url.endswith(row.slug)
    assert row.home_team == "Arsenal FC"
    assert row.watch is True


def test_list_upcoming_includes_disabled_ucl():
    epl = League("epl", "Premier League", "epl", "10188", 1, enabled=True)
    ucl = League("ucl", "UEFA Champions League", "ucl", "10204", 2, enabled=False)
    epl_fix = _fixture(league="epl", slug="epl-1", hours=10)
    ucl_fix = _fixture(league="ucl", slug="ucl-1", hours=12, title="Real Madrid vs. Bayern")

    class Fake:
        def list_moneyline_events(self, league):
            return [league.key]

        def parse_moneyline(self, league, event):
            return epl_fix if league.key == "epl" else ucl_fix

    rows = list_upcoming(Fake(), (epl, ucl), per_league=20, include_disabled=True)
    assert {row.league for row in rows} == {"epl", "ucl"}
    assert soonest(rows, 1)[0].slug == "epl-1"
    assert all(row.watch for row in rows)


def test_watchlist_matches_nicknames_and_typos():
    assert involves_watch_club(title="FC Barcelona vs. Elche CF", home_team="FC Barcelona", away_team="Elche CF")
    assert involves_watch_club(title="Real Madrid CF vs. Girona FC", home_team="Real Madrid CF")
    assert involves_watch_club(title="Club Atlético de Madrid vs. Sevilla", home_team="Club Atlético de Madrid")
    assert involves_watch_club(title="Tottenham Hotspur FC vs. Burnley", home_team="Tottenham Hotspur FC")
    assert involves_watch_club(title="Manchester City FC vs. Brighton", home_team="Manchester City FC")
    assert involves_watch_club(title="Manchester United FC vs. Burnley", home_team="Manchester United FC")
    assert involves_watch_club(title="Atalanta BC vs. Pisa", home_team="Atalanta BC")
    assert involves_watch_club(title="FC Bayern München vs. Leverkusen", home_team="FC Bayern München")
    assert involves_watch_club(title="Borussia Dortmund vs. Heidenheim", home_team="Borussia Dortmund")
    assert involves_watch_club(title="AC Milan vs. Genoa CFC", home_team="AC Milan")
    assert involves_watch_club(title="FC Internazionale Milano vs. Fiorentina", home_team="FC Internazionale Milano")
    assert involves_watch_club(title="Juventus FC vs. Parma", home_team="Juventus FC")
    assert involves_watch_club(title="Arsenal FC vs. Chelsea FC", home_team="Arsenal FC", away_team="Chelsea FC")
    assert involves_watch_club(title="Liverpool FC vs. Bournemouth", home_team="Liverpool FC")
    assert involves_watch_club(title="Paris Saint-Germain FC vs. Toulouse", home_team="Paris Saint-Germain FC")
    assert involves_watch_club(title="PSG vs. Lens", home_team="PSG")
    assert involves_watch_club(title="SSC Napoli vs. Cagliari Calcio", home_team="SSC Napoli")
    assert involves_watch_club(title="Napoli vs. Inter", home_team="Napoli")
    assert involves_watch_club(title="SSC Napoli vs. Como 1907", home_team="SSC Napoli", away_team="Como 1907")
    assert involves_watch_club(title="Udinese Calcio vs. Como 1907", home_team="Udinese Calcio", away_team="Como 1907")
    assert involves_watch_club(title="SS Lazio vs. Bologna FC", home_team="SS Lazio")
    assert involves_watch_club(title="Lazio vs. Roma", home_team="Lazio")


def test_watchlist_does_not_cross_match_other_clubs():
    assert not involves_watch_club(title="Athletic Club vs. Elche CF", home_team="Athletic Club", away_team="Elche CF")
    assert not involves_watch_club(title="Bayer 04 Leverkusen vs. Mainz 05", home_team="Bayer 04 Leverkusen")
    assert not involves_watch_club(title="Paris FC vs. Nantes", home_team="Paris FC", away_team="Nantes")
    assert not involves_watch_club(
        title="Real Sociedad de Fútbol vs. RCD Espanyol de Barcelona",
        home_team="Real Sociedad de Fútbol",
        away_team="RCD Espanyol de Barcelona",
    )
    assert from_fixture(_fixture(title="Getafe CF vs. Valencia CF", slug="lal-get-val"), "LaLiga").watch is False
