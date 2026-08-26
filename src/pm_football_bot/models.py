from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class OutcomeBook:
    token_id: str
    label: str
    mid: float | None
    best_bid: float | None
    best_ask: float | None

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return round(self.best_ask - self.best_bid, 6)


@dataclass(frozen=True)
class BinaryMarket:
    question: str
    slug: str
    kind: str
    line: float | None
    outcomes: tuple[OutcomeBook, ...]
    accepting_orders: bool = True

    def outcome(self, label: str) -> OutcomeBook | None:
        wanted = label.lower()
        for row in self.outcomes:
            if row.label.lower() == wanted:
                return row
        return None


@dataclass(frozen=True)
class SideQuote:
    team: str
    yes: OutcomeBook
    no: OutcomeBook


@dataclass(frozen=True)
class Fixture:
    league: str
    title: str
    slug: str
    kickoff: datetime | None
    home: SideQuote | None
    away: SideQuote | None
    draw: BinaryMarket | None
    extras: tuple[BinaryMarket, ...] = ()

    @property
    def dog(self) -> SideQuote | None:
        if self.home is None or self.away is None:
            return None
        home_yes = self.home.yes.mid
        away_yes = self.away.yes.mid
        if home_yes is None or away_yes is None:
            return None
        return self.home if home_yes <= away_yes else self.away

    @property
    def favorite(self) -> SideQuote | None:
        if self.home is None or self.away is None:
            return None
        dog = self.dog
        if dog is None:
            return None
        return self.away if dog is self.home else self.home


@dataclass(frozen=True)
class Ticket:
    league: str
    fixture: str
    slug: str
    rule_id: str
    question: str
    token_id: str
    outcome: str
    price: float
    shares: float
    cost_usd: float
    spread: float | None
    reason: str
    meaning: str = ""
    dog_team: str = ""
    favorite_team: str = ""
    dog_yes: float | None = None
    favorite_yes: float | None = None
    kickoff: datetime | None = None
