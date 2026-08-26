from __future__ import annotations

from pm_football_bot.config import Settings
from pm_football_bot.models import Ticket


def size_book(tickets: list[Ticket], settings: Settings) -> list[Ticket]:
    """Keep the highest-priced (safest harvest) tickets under the open-risk cap."""
    ranked = sorted(tickets, key=lambda t: (-t.price, t.spread or 99, t.fixture))
    kept: list[Ticket] = []
    spent = 0.0
    per_fixture: dict[str, int] = {}

    for ticket in ranked:
        if spent + ticket.cost_usd > settings.max_open_usd:
            continue
        if spent + ticket.cost_usd > settings.bankroll_usd:
            continue
        count = per_fixture.get(ticket.slug, 0)
        if count >= settings.max_tickets_per_fixture:
            continue
        kept.append(ticket)
        spent += ticket.cost_usd
        per_fixture[ticket.slug] = count + 1

    return kept
