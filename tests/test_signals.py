from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pm_football_bot.config import load_settings
from pm_football_bot.gamma import invert_book
from pm_football_bot.models import BinaryMarket, Fixture, OutcomeBook, SideQuote
from pm_football_bot.signals import _is_game_totals, is_mismatch, propose_tickets


def _book(token: str, label: str, mid: float, bid: float, ask: float) -> OutcomeBook:
    return OutcomeBook(token_id=token, label=label, mid=mid, best_bid=bid, best_ask=ask)


def _side(team: str, yes_mid: float, yes_bid: float, yes_ask: float) -> SideQuote:
    no_bid, no_ask = invert_book(yes_bid, yes_ask)
    return SideQuote(
        team=team,
        yes=_book(f"{team}-yes", "Yes", yes_mid, yes_bid, yes_ask),
        no=_book(f"{team}-no", "No", round(1 - yes_mid, 4), no_bid, no_ask),
    )


def _totals(line: float, over_mid: float, over_bid: float, over_ask: float) -> BinaryMarket:
    under_bid, under_ask = invert_book(over_bid, over_ask)
    return BinaryMarket(
        question=f"O/U {line}",
        slug=f"ou-{line}",
        kind="totals",
        line=line,
        outcomes=(
            _book(f"ou-{line}-over", "Over", over_mid, over_bid, over_ask),
            _book(f"ou-{line}-under", "Under", round(1 - over_mid, 4), under_bid, under_ask),
        ),
    )


def _fixture(*, dog_yes: float, fav_yes: float, hours: float = 48) -> Fixture:
    kickoff = datetime.now(timezone.utc) + timedelta(hours=hours)
    return Fixture(
        league="epl",
        title="Arsenal FC vs. Coventry City FC",
        slug="epl-ars-cov-2026-08-21",
        kickoff=kickoff,
        home=_side("Arsenal FC", fav_yes, fav_yes - 0.005, fav_yes + 0.005),
        away=_side("Coventry City FC", dog_yes, max(0.01, dog_yes - 0.005), dog_yes + 0.005),
        draw=None,
        extras=(
            _totals(0.5, 0.958, 0.956, 0.960),
            _totals(5.5, 0.10, 0.09, 0.11),
        ),
    )


def test_invert_book():
    assert invert_book(0.04, 0.05) == (0.95, 0.96)


def test_mismatch_detects_promoted_dog():
    settings = load_settings()
    assert is_mismatch(_fixture(dog_yes=0.045, fav_yes=0.835), settings)
    assert not is_mismatch(_fixture(dog_yes=0.32, fav_yes=0.41), settings)


def test_mismatch_emits_three_harvest_tickets():
    settings = load_settings()
    tickets = propose_tickets(_fixture(dog_yes=0.045, fav_yes=0.835), settings)
    ids = [t.rule_id for t in tickets]
    assert ids == ["fade_dog", "over_0_5", "under_5_5"]
    assert all(settings.price_min <= t.price <= settings.price_max for t in tickets)
    assert all(t.shares >= settings.min_shares for t in tickets)


def test_even_game_emits_nothing():
    settings = load_settings()
    tickets = propose_tickets(_fixture(dog_yes=0.32, fav_yes=0.41), settings)
    assert tickets == []


def test_game_totals_kind_aliases():
    assert _is_game_totals("totals")
    assert _is_game_totals("total")
    assert _is_game_totals("over_under")
    assert not _is_game_totals("soccer_team_totals")
    assert not _is_game_totals("first_half_totals")


def test_mismatch_emits_totals_when_gamma_kind_is_total():
    settings = load_settings()
    fixture = _fixture(dog_yes=0.045, fav_yes=0.835)
    extras = []
    for market in fixture.extras:
        extras.append(
            BinaryMarket(
                question=market.question,
                slug=market.slug,
                kind="total",
                line=market.line,
                outcomes=market.outcomes,
            )
        )
    fixture = Fixture(
        league=fixture.league,
        title=fixture.title,
        slug=fixture.slug,
        kickoff=fixture.kickoff,
        home=fixture.home,
        away=fixture.away,
        draw=fixture.draw,
        extras=tuple(extras),
    )
    ids = [t.rule_id for t in propose_tickets(fixture, settings)]
    assert ids == ["fade_dog", "over_0_5", "under_5_5"]
