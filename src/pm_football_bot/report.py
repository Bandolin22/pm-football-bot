from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from pm_football_bot.config import Settings
from pm_football_bot.models import Ticket, utcnow


@dataclass(frozen=True)
class ReviewedTicket:
    ticket: Ticket
    verdict: str
    why: str

    @property
    def if_win_usd(self) -> float:
        return round(self.ticket.shares, 2)

    @property
    def profit_if_win_usd(self) -> float:
        return round(self.ticket.shares - self.ticket.cost_usd, 2)


@dataclass
class ScanResult:
    settings: Settings
    scanned: int
    mismatches: int
    tickets: list[Ticket]
    as_of: datetime = field(default_factory=utcnow)

    @property
    def reviewed(self) -> list[ReviewedTicket]:
        return [review_ticket(t, self.settings) for t in self.tickets]

    def by_verdict(self, verdict: str) -> list[ReviewedTicket]:
        return [row for row in self.reviewed if row.verdict == verdict]


def review_ticket(ticket: Ticket, settings: Settings | None = None) -> ReviewedTicket:
    title = ticket.fixture.lower()
    reason = ticket.reason.lower()
    if "first team to score" in title or " vs neither" in reason or reason.endswith("vs neither"):
        return ReviewedTicket(
            ticket,
            "skip",
            "Wrong market (first scorer / neither). Not the 90-minute win harvest.",
        )
    if "exact score" in title:
        return ReviewedTicket(ticket, "skip", "Exact-score spray. Skip on $400.")
    max_dog = settings.max_dog_yes if settings is not None else 0.12
    min_fav = settings.min_favorite_yes if settings is not None else 0.70
    dog = ticket.dog_yes
    fav = ticket.favorite_yes
    if dog is not None and fav is not None and (dog > max_dog or fav < min_fav):
        return ReviewedTicket(
            ticket,
            "borderline",
            f"Outside the {max_dog * 100:.0f}¢ dog / {min_fav * 100:.0f}¢ favorite cutoff.",
        )
    return ReviewedTicket(ticket, "keep", "Core harvest ticket on a real mismatch.")


def group_by_fixture(rows: list[ReviewedTicket]) -> list[tuple[str, list[ReviewedTicket]]]:
    buckets: dict[str, list[ReviewedTicket]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        key = row.ticket.slug or row.ticket.fixture
        if key not in buckets:
            order.append(key)
        buckets[key].append(row)
    ranked = sorted(
        order,
        key=lambda k: (
            0 if any(r.verdict == "keep" for r in buckets[k]) else 1,
            min((r.ticket.dog_yes if r.ticket.dog_yes is not None else 9) for r in buckets[k]),
        ),
    )
    return [(k, buckets[k]) for k in ranked]


def planned_usd(rows: list[ReviewedTicket]) -> float:
    return round(sum(r.ticket.cost_usd for r in rows), 2)


def profit_if_all_win(rows: list[ReviewedTicket]) -> float:
    return round(sum(r.profit_if_win_usd for r in rows), 2)
