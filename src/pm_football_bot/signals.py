from __future__ import annotations

from datetime import datetime, timezone

from pm_football_bot.config import Settings
from pm_football_bot.models import BinaryMarket, Fixture, Ticket, utcnow

_MEANING = {
    "fade_dog": "This underdog does not win in 90 minutes.",
    "over_0_5": "Someone scores — the match is not 0–0.",
    "under_5_5": "Combined goals stay under 6.",
}


def _in_kickoff_window(fixture: Fixture, settings: Settings, now: datetime) -> bool:
    if fixture.kickoff is None:
        return False
    hours = (fixture.kickoff - now).total_seconds() / 3600
    if hours < settings.min_hours_to_kickoff:
        return False
    if hours > settings.max_days_to_kickoff * 24:
        return False
    return True


def is_mismatch(fixture: Fixture, settings: Settings) -> bool:
    dog = fixture.dog
    fav = fixture.favorite
    if dog is None or fav is None or dog.yes.mid is None or fav.yes.mid is None:
        return False
    return dog.yes.mid <= settings.max_dog_yes and fav.yes.mid >= settings.min_favorite_yes


def _is_game_totals(kind: str) -> bool:
    """Match full-match O/U only. Do not treat team/half totals as the harvest lines."""
    return kind.lower().replace("-", "_") in {"totals", "total", "over_under", "overunder"}


def _quote_ok(bid: float | None, spread: float | None, settings: Settings) -> bool:
    if bid is None or spread is None:
        return False
    if spread > settings.max_spread:
        return False
    if bid < settings.min_best_bid:
        return False
    if bid < settings.price_min or bid > settings.price_max:
        return False
    return True


def _ticket(
    fixture: Fixture,
    rule_id: str,
    market: BinaryMarket,
    outcome_label: str,
    reason: str,
    settings: Settings,
) -> Ticket | None:
    book = market.outcome(outcome_label)
    if book is None or not market.accepting_orders:
        return None
    if not _quote_ok(book.best_bid, book.spread, settings):
        return None
    price = book.best_bid
    assert price is not None
    shares = max(settings.min_shares, round(settings.ticket_usd / price, 2))
    if shares < settings.min_shares:
        return None
    cost = round(shares * price, 2)
    dog = fixture.dog
    fav = fixture.favorite
    return Ticket(
        league=fixture.league,
        fixture=fixture.title,
        slug=fixture.slug,
        rule_id=rule_id,
        question=market.question,
        token_id=book.token_id,
        outcome=outcome_label,
        price=price,
        shares=shares,
        cost_usd=cost,
        spread=book.spread,
        reason=reason,
        meaning=_MEANING.get(rule_id, market.question),
        dog_team=dog.team if dog else "",
        favorite_team=fav.team if fav else "",
        dog_yes=dog.yes.mid if dog else None,
        favorite_yes=fav.yes.mid if fav else None,
        kickoff=fixture.kickoff,
    )


def propose_tickets(
    fixture: Fixture,
    settings: Settings,
    now: datetime | None = None,
) -> list[Ticket]:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not _in_kickoff_window(fixture, settings, now):
        return []
    if not is_mismatch(fixture, settings):
        return []

    dog = fixture.dog
    fav = fixture.favorite
    assert dog is not None and fav is not None
    reason = (
        f"mismatch {fav.team} {fav.yes.mid:.3f} vs {dog.team} {dog.yes.mid:.3f}"
    )

    tickets: list[Ticket] = []
    for rule in settings.rules:
        if not rule.enabled:
            continue
        if rule.kind == "moneyline_dog" and (rule.side or "no").lower() == "no":
            market = BinaryMarket(
                question=f"Will {dog.team} win?",
                slug=fixture.slug,
                kind="moneyline",
                line=None,
                outcomes=(dog.yes, dog.no),
            )
            ticket = _ticket(fixture, rule.id, market, "No", reason, settings)
            if ticket:
                tickets.append(ticket)
            continue

        if rule.kind == "totals" and rule.line is not None and rule.side:
            side = "Over" if rule.side.lower() == "over" else "Under"
            for market in fixture.extras:
                if not _is_game_totals(market.kind):
                    continue
                if market.line != rule.line:
                    continue
                ticket = _ticket(fixture, rule.id, market, side, reason, settings)
                if ticket:
                    tickets.append(ticket)
                break

    return tickets[: settings.max_tickets_per_fixture]
