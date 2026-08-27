from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import requests

from pm_football_bot.config import League, Settings
from pm_football_bot.models import BinaryMarket, Fixture, OutcomeBook, SideQuote


def _parse_maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_kickoff(event: dict[str, Any]) -> datetime | None:
    raw = event.get("startTime") or event.get("endDate")
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


NOISE_TITLE_BITS = (
    "first team to score",
    "exact score",
    "more markets",
    "both teams to score",
    "correct score",
    "anytime goalscorer",
    "halftime result",
    "second half result",
    "total corners",
    "player props",
)

NOISE_SLUG_BITS = (
    "-more-markets",
    "exact-score",
    "first-team",
    "-btts",
    "-ou-",
    "-spread",
    "-halftime-result",
    "-second-half-result",
    "-total-corners",
    "-first-to-score",
    "-player-props",
)


def is_primary_moneyline_event(event: dict[str, Any]) -> bool:
    """Keep 90-minute home/draw/away events; drop first-scorer and side books."""
    title = str(event.get("title") or "").lower()
    slug = str(event.get("slug") or "").lower()
    if any(bit in title for bit in NOISE_TITLE_BITS):
        return False
    if any(bit in slug for bit in NOISE_SLUG_BITS):
        return False
    return True


def invert_book(yes_bid: float | None, yes_ask: float | None) -> tuple[float | None, float | None]:
    """Approximate the No/Under book from the Yes/Over bid-ask."""
    no_bid = round(1 - yes_ask, 4) if yes_ask is not None else None
    no_ask = round(1 - yes_bid, 4) if yes_bid is not None else None
    return no_bid, no_ask


def _binary_market(raw: dict[str, Any], kind: str, line: float | None = None) -> BinaryMarket | None:
    labels = _parse_maybe_json(raw.get("outcomes")) or []
    prices = _parse_maybe_json(raw.get("outcomePrices")) or []
    tokens = _parse_maybe_json(raw.get("clobTokenIds")) or []
    if not (isinstance(labels, list) and isinstance(tokens, list) and len(labels) == 2 and len(tokens) == 2):
        return None

    yes_bid = _as_float(raw.get("bestBid"))
    yes_ask = _as_float(raw.get("bestAsk"))
    mids = [_as_float(p) for p in prices] if isinstance(prices, list) else [None, None]
    while len(mids) < 2:
        mids.append(None)

    other_bid, other_ask = invert_book(yes_bid, yes_ask)
    books = (
        OutcomeBook(
            token_id=str(tokens[0]),
            label=str(labels[0]),
            mid=mids[0],
            best_bid=yes_bid,
            best_ask=yes_ask,
        ),
        OutcomeBook(
            token_id=str(tokens[1]),
            label=str(labels[1]),
            mid=mids[1],
            best_bid=other_bid,
            best_ask=other_ask,
        ),
    )
    return BinaryMarket(
        question=str(raw.get("question") or raw.get("groupItemTitle") or ""),
        slug=str(raw.get("slug") or ""),
        kind=kind,
        line=line if line is not None else _as_float(raw.get("line")),
        outcomes=books,
        accepting_orders=bool(raw.get("acceptingOrders", True)),
    )


class GammaClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.settings.gamma_host}{path}"
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def list_moneyline_events(self, league: League) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get(
                "/events",
                {
                    "series_id": league.series_id,
                    "active": "true",
                    "closed": "false",
                    "limit": 50,
                    "offset": offset,
                },
            )
            if not isinstance(page, list) or not page:
                break
            rows.extend(item for item in page if is_primary_moneyline_event(item))
            if len(page) < 50:
                break
            offset += 50
        return rows

    def fetch_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        data = self._get("/events", {"slug": slug})
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict) and data.get("slug"):
            return data
        return None

    def parse_moneyline(self, league: League, event: dict[str, Any]) -> Fixture:
        markets = event.get("markets") or []
        home: SideQuote | None = None
        away: SideQuote | None = None
        draw: BinaryMarket | None = None
        teams = event.get("teams") or []
        home_name = next((t.get("name") for t in teams if t.get("ordering") == "home"), None)
        away_name = next((t.get("name") for t in teams if t.get("ordering") == "away"), None)

        for raw in markets:
            parsed = _binary_market(raw, "moneyline")
            if parsed is None:
                continue
            question = parsed.question.lower()
            title = str(raw.get("groupItemTitle") or "")
            if "draw" in question or title.lower().startswith("draw"):
                draw = parsed
                continue
            yes = parsed.outcome("Yes")
            no = parsed.outcome("No")
            if yes is None or no is None:
                continue
            team = title or parsed.question
            quote = SideQuote(team=team, yes=yes, no=no)
            if home_name and home_name.lower() in team.lower():
                home = quote
            elif away_name and away_name.lower() in team.lower():
                away = quote
            elif home is None:
                home = quote
            else:
                away = quote

        return Fixture(
            league=league.key,
            title=str(event.get("title") or event.get("slug") or ""),
            slug=str(event.get("slug") or ""),
            kickoff=_parse_kickoff(event),
            home=home,
            away=away,
            draw=draw,
            extras=(),
        )

    def attach_more_markets(self, fixture: Fixture) -> Fixture:
        more = self.fetch_event_by_slug(f"{fixture.slug}-more-markets")
        extras: list[BinaryMarket] = []
        if more:
            for raw in more.get("markets") or []:
                kind = str(raw.get("sportsMarketType") or "other")
                parsed = _binary_market(raw, kind, _as_float(raw.get("line")))
                if parsed is not None:
                    extras.append(parsed)
        return replace(fixture, extras=tuple(extras))

    def build_fixture(self, league: League, event: dict[str, Any]) -> Fixture:
        return self.attach_more_markets(self.parse_moneyline(league, event))
