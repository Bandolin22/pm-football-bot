from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import requests

from pm_football_bot.models import Ticket

SWISSTONY = "0x204f72f35326db932158cba6adff0b9a1da95e14"
DATA_API = "https://data-api.polymarket.com"
PROFILE = f"https://polymarket.com/@swisstony"

_PARENT = re.compile(
    r"^((?:epl|lal|ucl|fl1|sea|bun)-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SwissLot:
    league: str
    parent: str
    event_slug: str
    title: str
    outcome: str
    shares_bought: float
    avg_price: float
    cost_usd: float
    shares_now: float
    mark_usd: float
    u_pnl: float
    cur_price: float | None

    @property
    def avg_cents(self) -> float:
        return self.avg_price * 100


def parent_slug(slug: str) -> str:
    text = (slug or "").strip().lower()
    match = _PARENT.match(text)
    if match:
        return match.group(1)
    return text


def league_of(slug: str) -> str | None:
    key = parent_slug(slug)
    if key.startswith("epl-"):
        return "epl"
    if key.startswith("lal-") or key.startswith("laliga-"):
        return "laliga"
    if key.startswith("fl1-"):
        return "ligue1"
    if key.startswith("sea-"):
        return "seriea"
    if key.startswith("bun-"):
        return "bundesliga"
    if key.startswith("ucl-"):
        return "ucl"
    return None


def _lot_from_position(raw: dict[str, Any]) -> SwissLot | None:
    slug = str(raw.get("eventSlug") or raw.get("slug") or "")
    league = league_of(slug)
    if league is None:
        return None
    bought = float(raw.get("totalBought") or 0)
    avg = float(raw.get("avgPrice") or 0)
    return SwissLot(
        league=league,
        parent=parent_slug(slug),
        event_slug=slug,
        title=str(raw.get("title") or ""),
        outcome=str(raw.get("outcome") or ""),
        shares_bought=bought,
        avg_price=avg,
        cost_usd=round(bought * avg, 2),
        shares_now=float(raw.get("size") or 0),
        mark_usd=float(raw.get("currentValue") or 0),
        u_pnl=float(raw.get("cashPnl") or 0),
        cur_price=float(raw["curPrice"]) if raw.get("curPrice") is not None else None,
    )


def fetch_positions(wallet: str = SWISSTONY, session: requests.Session | None = None) -> list[dict[str, Any]]:
    client = session or requests.Session()
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = client.get(
            f"{DATA_API}/positions",
            params={"user": wallet, "sizeThreshold": 0, "limit": 500, "offset": offset},
            timeout=60,
        )
        response.raise_for_status()
        page = response.json()
        if isinstance(page, dict):
            page = page.get("positions") or page.get("data") or []
        if not page:
            break
        rows.extend(page)
        if len(page) < 500:
            break
        offset += 500
        if offset > 8000:
            break
    return rows


def load_book(wallet: str = SWISSTONY) -> list[SwissLot]:
    lots = []
    for raw in fetch_positions(wallet):
        lot = _lot_from_position(raw)
        if lot is not None and lot.shares_bought > 0:
            lots.append(lot)
    return sorted(lots, key=lambda row: -row.cost_usd)


def lots_for_parent(lots: list[SwissLot], slug: str) -> list[SwissLot]:
    key = parent_slug(slug)
    return [lot for lot in lots if lot.parent == key]


def _after_colon(title: str) -> str:
    if ":" not in title:
        return title.strip().lower()
    return title.split(":")[-1].strip().lower()


def matches_harvest(ticket: Ticket, lot: SwissLot) -> bool:
    title = lot.title.lower()
    tail = _after_colon(lot.title)
    if ticket.rule_id == "fade_dog":
        dog = (ticket.dog_team or "").lower()
        return "win on" in title and lot.outcome == "No" and (not dog or dog in title)
    if ticket.rule_id == "over_0_5":
        return tail.startswith("o/u 0.5") and lot.outcome == "Over"
    if ticket.rule_id == "under_5_5":
        return tail.startswith("o/u 5.5") and lot.outcome == "Under"
    return False


def matched_lot(ticket: Ticket, lots: list[SwissLot]) -> SwissLot | None:
    hits = [lot for lot in lots if matches_harvest(ticket, lot)]
    if not hits:
        return None
    return max(hits, key=lambda row: row.cost_usd)


def fixture_totals(lots: list[SwissLot]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for lot in lots:
        totals[lot.parent] += lot.cost_usd
    return dict(totals)
