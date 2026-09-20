from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import yaml

from pm_football_bot.board import is_upcoming, matched_watch_club
from pm_football_bot.config import CONFIG_DIR, ROOT, Settings, load_settings
from pm_football_bot.execution import LiveTradingDisabled, live_client, place_ticket
from pm_football_bot.gamma import GammaClient
from pm_football_bot.models import BinaryMarket, Fixture, OutcomeBook, SideQuote, Ticket, utcnow
from pm_football_bot.scout import Briefing, TeamPulse, _name_score, fold_name, load_briefing, split_fixture
from pm_football_bot.signals import _is_game_totals

ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class KeeperScan:
    settings: Settings
    cfg: KeeperConfig
    tickets: list[Ticket]
    watched: int
    fixtures: tuple[str, ...]
    as_of: datetime

_H2H_SCORE = re.compile(
    r"^(?:[A-Za-z]{3}\s+\d{4}\s+)?(?P<home>.+?)\s+(?P<hs>\d+)\s*[–-]\s*(?P<as>\d+)\s+(?P<away>.+)$"
)
_LAST_FIVE = re.compile(
    r"^[WLD\?]\s+(?P<home>.+?)\s+(?P<hs>\d+)\s*[–-]\s*(?P<as>\d+)\s+(?P<away>.+)$"
)

_MEANING = {
    "keeper_fade_dog": "Watchlist favorite: underdog does not win in 90 minutes.",
    "keeper_over_0_5": "Someone scores — cheapest Over 0.5 equivalent.",
    "keeper_over_1_5": "At least two goals (Over 0.5 books were 98c+).",
    "keeper_under_5_5": "Combined goals stay under 6.",
    "keeper_exact_else_no": "Score stays in 0-0 through 3-3 (Any Other Score No).",
    "keeper_corners_7_5": "Two watchlist sides: Over 7.5 corners near 75%.",
}


@dataclass(frozen=True)
class KeeperConfig:
    shares: float
    max_price: float
    hard_price: float
    home_favorite_min: float
    away_favorite_min: float
    dog_yes_max: float
    over_line: float
    over_fallback_line: float
    under_line: float
    corner_line: float
    corner_min: float
    corner_max: float
    corners: bool
    prolific_gf_pg: float
    prolific_match_goals: float
    fav_min_wins: int
    dog_max_wins: int
    shy_gf_pg: float = 0.8


def load_keeper_config(path: Path | None = None) -> KeeperConfig:
    target = path or (CONFIG_DIR / "keeper.yaml")
    with target.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{target} must contain a mapping")
    return KeeperConfig(
        shares=float(raw.get("shares", 5)),
        max_price=float(raw.get("max_price", 0.979)),
        hard_price=float(raw.get("hard_price", 0.98)),
        home_favorite_min=float(raw.get("home_favorite_min", 0.80)),
        away_favorite_min=float(raw.get("away_favorite_min", 0.85)),
        dog_yes_max=float(raw.get("dog_yes_max", 0.12)),
        over_line=float(raw.get("over_line", 0.5)),
        over_fallback_line=float(raw.get("over_fallback_line", 1.5)),
        under_line=float(raw.get("under_line", 5.5)),
        corners=bool(raw.get("corners", False)),
        corner_line=float(raw.get("corner_line", 7.5)),
        corner_min=float(raw.get("corner_min", 0.70)),
        corner_max=float(raw.get("corner_max", 0.80)),
        prolific_gf_pg=float(raw.get("prolific_gf_pg", 2.2)),
        prolific_match_goals=float(raw.get("prolific_match_goals", 4.0)),
        fav_min_wins=int(raw.get("fav_min_wins", 3)),
        dog_max_wins=int(raw.get("dog_max_wins", 1)),
        shy_gf_pg=float(raw.get("shy_gf_pg", 0.8)),
    )


_REBUY_OK = {"cancelled", "canceled", "expired"}
_FILLED = {"filled", "matched"}
_WORKING = {"posted", "live", "partial", "delayed", "unknown"}


@dataclass
class PlacedOrder:
    key: str
    status: str = "unknown"
    order_id: str = ""
    size_matched: float = 0.0
    shares: float = 0.0
    price: float = 0.0
    token_id: str = ""
    rule: str = ""
    fixture: str = ""
    slug: str = ""
    question: str = ""
    outcome: str = ""
    league: str = ""
    meaning: str = ""
    reason: str = ""
    posted_at: str = ""
    kickoff: str = ""

    @property
    def filled(self) -> bool:
        return self.status in _FILLED or (
            self.shares > 0 and self.size_matched + 1e-9 >= self.shares and self.status not in _REBUY_OK
        )

    @property
    def working(self) -> bool:
        return (not self.filled) and self.status in _WORKING | {"posted"}

    @property
    def blocks_rebuy(self) -> bool:
        return self.status not in _REBUY_OK

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "status": self.status,
            "order_id": self.order_id,
            "size_matched": self.size_matched,
            "shares": self.shares,
            "price": self.price,
            "token_id": self.token_id,
            "rule": self.rule,
            "fixture": self.fixture,
            "slug": self.slug,
            "question": self.question,
            "outcome": self.outcome,
            "league": self.league,
            "meaning": self.meaning,
            "reason": self.reason,
            "posted_at": self.posted_at,
            "kickoff": self.kickoff,
        }


def placed_path() -> Path:
    override = (os.environ.get("KEEPER_STATE_PATH") or "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "keeper" / "placed.json"


def _as_float(raw: object) -> float:
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def order_id_from_result(raw: object) -> str:
    if not isinstance(raw, dict):
        return ""
    for key in ("orderID", "orderId", "order_id", "id"):
        value = raw.get(key)
        if value:
            return str(value)
    nested = raw.get("order")
    if isinstance(nested, dict):
        return order_id_from_result(nested)
    return ""


def classify_clob_status(payload: dict[str, object] | None) -> str:
    if not payload:
        return "unknown"
    raw = str(payload.get("status") or "").strip().lower()
    matched = _as_float(payload.get("size_matched") or payload.get("sizeMatched"))
    original = _as_float(payload.get("original_size") or payload.get("originalSize"))
    hashes = payload.get("transactionsHashes") or payload.get("transactionHashes")
    if hashes:
        return "filled"
    if raw in {"matched", "filled"}:
        return "filled"
    if original > 0 and matched + 1e-9 >= original:
        return "filled"
    if raw in {"canceled", "cancelled"}:
        return "partial" if matched > 0 else "cancelled"
    if raw in {"expired"}:
        return "expired"
    if matched > 0:
        return "partial"
    if raw in {"live", "delayed", "unmatched", ""}:
        return "live"
    return raw or "unknown"


def _order_from_raw(key: str, raw: object) -> PlacedOrder:
    if isinstance(raw, dict):
        return PlacedOrder(
            key=str(raw.get("key") or key),
            status=str(raw.get("status") or "unknown"),
            order_id=str(raw.get("order_id") or ""),
            size_matched=_as_float(raw.get("size_matched")),
            shares=_as_float(raw.get("shares")),
            price=_as_float(raw.get("price")),
            token_id=str(raw.get("token_id") or ""),
            rule=str(raw.get("rule") or ""),
            fixture=str(raw.get("fixture") or ""),
            slug=str(raw.get("slug") or ""),
            question=str(raw.get("question") or ""),
            outcome=str(raw.get("outcome") or ""),
            league=str(raw.get("league") or ""),
            meaning=str(raw.get("meaning") or ""),
            reason=str(raw.get("reason") or ""),
            posted_at=str(raw.get("posted_at") or ""),
            kickoff=str(raw.get("kickoff") or ""),
        )
    return PlacedOrder(key=str(key), status="unknown")


def load_placed_book(path: Path | None = None) -> dict[str, PlacedOrder]:
    target = path or placed_path()
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(raw, list):
        return {str(item): PlacedOrder(key=str(item), status="unknown") for item in raw}
    if isinstance(raw, dict):
        return {str(key): _order_from_raw(str(key), value) for key, value in raw.items()}
    return {}


def save_placed_book(book: dict[str, PlacedOrder], path: Path | None = None) -> None:
    target = path or placed_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: row.to_dict() for key, row in sorted(book.items())}
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_placed(path: Path | None = None) -> set[str]:
    return {key for key, row in load_placed_book(path).items() if row.blocks_rebuy}


def save_placed(keys: set[str], path: Path | None = None) -> None:
    existing = load_placed_book(path)
    book = {key: existing.get(key) or PlacedOrder(key=key, status="unknown") for key in keys}
    save_placed_book(book, path)


def ticket_key(ticket: Ticket) -> str:
    return f"{ticket.slug}:{ticket.rule_id}"


def open_tickets(tickets: list[Ticket], already: set[str] | None = None) -> list[Ticket]:
    """Drop tickets that already have a live or filled GTC."""
    placed = already if already is not None else load_placed()
    return [ticket for ticket in tickets if ticket_key(ticket) not in placed]


def placed_order_from_ticket(ticket: Ticket, raw: object) -> PlacedOrder:
    payload = raw if isinstance(raw, dict) else {}
    status = classify_clob_status(payload)
    if status in {"unknown", "live"} and not payload.get("status") and not payload.get("id"):
        ack = str(payload.get("status") or "live").lower()
        status = "filled" if ack in _FILLED else "live"
    kickoff = ticket.kickoff.astimezone(timezone.utc).isoformat() if ticket.kickoff else ""
    return PlacedOrder(
        key=ticket_key(ticket),
        status=status,
        order_id=order_id_from_result(payload),
        size_matched=_as_float(payload.get("size_matched") or payload.get("sizeMatched")),
        shares=ticket.shares,
        price=ticket.price,
        token_id=ticket.token_id,
        rule=ticket.rule_id,
        fixture=ticket.fixture,
        slug=ticket.slug,
        question=ticket.question,
        outcome=ticket.outcome,
        league=ticket.league,
        meaning=ticket.meaning,
        reason=ticket.reason,
        posted_at=utcnow().isoformat(),
        kickoff=kickoff,
    )


def _index_open_orders(rows: list[object]) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    by_id: dict[str, dict[str, object]] = {}
    by_asset: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        oid = str(row.get("id") or row.get("orderID") or row.get("order_id") or "")
        asset = str(row.get("asset_id") or row.get("assetId") or "")
        if oid:
            by_id[oid] = row
        if asset and asset not in by_asset:
            by_asset[asset] = row
    return by_id, by_asset


def _trade_hits(rows: list[object]) -> tuple[set[str], set[str]]:
    order_ids: set[str] = set()
    assets: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        assets.add(str(row.get("asset_id") or row.get("assetId") or ""))
        for key in ("taker_order_id", "takerOrderId", "id"):
            value = row.get(key)
            if value:
                order_ids.add(str(value))
        makers = row.get("maker_orders") or row.get("makerOrders") or []
        if isinstance(makers, list):
            for maker in makers:
                if not isinstance(maker, dict):
                    continue
                oid = maker.get("order_id") or maker.get("orderId") or maker.get("id")
                if oid:
                    order_ids.add(str(oid))
                asset = maker.get("asset_id") or maker.get("assetId")
                if asset:
                    assets.add(str(asset))
    assets.discard("")
    return order_ids, assets


def refresh_placed_orders(
    settings: Settings,
    tickets: list[Ticket] | None = None,
    *,
    client: object | None = None,
) -> dict[str, PlacedOrder]:
    """Ask the CLOB which posted GTCs are still live vs filled."""
    book = load_placed_book()
    if not book:
        return book
    handle = client
    if handle is None:
        try:
            handle = live_client(settings)
        except LiveTradingDisabled:
            return book
        except Exception:
            return book
    try:
        open_rows = list(handle.get_open_orders() or [])
    except Exception:
        return book
    by_id, by_asset = _index_open_orders(open_rows)
    trade_order_ids: set[str] = set()
    trade_assets: set[str] = set()
    try:
        getter = getattr(handle, "get_trades", None)
        if callable(getter):
            trade_order_ids, trade_assets = _trade_hits(list(getter(only_first_page=True) or []))
    except Exception:
        pass

    ticket_by_key = {ticket_key(ticket): ticket for ticket in tickets or []}
    for key, rec in book.items():
        ticket = ticket_by_key.get(key)
        if ticket is not None:
            rec.token_id = rec.token_id or ticket.token_id
            rec.fixture = rec.fixture or ticket.fixture
            rec.question = rec.question or ticket.question
            rec.outcome = rec.outcome or ticket.outcome
            rec.rule = rec.rule or ticket.rule_id
            rec.slug = rec.slug or ticket.slug
            rec.league = rec.league or ticket.league
            rec.shares = rec.shares or ticket.shares
            rec.price = rec.price or ticket.price
            rec.meaning = rec.meaning or ticket.meaning
            rec.reason = rec.reason or ticket.reason
            if not rec.kickoff and ticket.kickoff:
                rec.kickoff = ticket.kickoff.astimezone(timezone.utc).isoformat()

        payload = None
        if rec.order_id and rec.order_id in by_id:
            payload = by_id[rec.order_id]
        elif rec.token_id and rec.token_id in by_asset:
            payload = by_asset[rec.token_id]
            rec.order_id = rec.order_id or str(payload.get("id") or "")
        elif rec.order_id:
            try:
                fetched = handle.get_order(rec.order_id)
                if isinstance(fetched, dict):
                    payload = fetched
            except Exception:
                payload = None

        if payload:
            rec.status = classify_clob_status(payload)
            rec.size_matched = _as_float(payload.get("size_matched") or payload.get("sizeMatched") or rec.size_matched)
            original = _as_float(payload.get("original_size") or payload.get("originalSize"))
            if original:
                rec.shares = original
            rec.token_id = rec.token_id or str(payload.get("asset_id") or payload.get("assetId") or "")
            continue
        if rec.order_id and rec.order_id in trade_order_ids:
            rec.status = "filled"
            rec.size_matched = rec.size_matched or rec.shares
            continue
        if rec.token_id and rec.token_id in trade_assets and rec.status in _WORKING:
            rec.status = "filled"
            rec.size_matched = rec.size_matched or rec.shares
            continue
        if rec.order_id and rec.status in {"live", "partial", "posted"}:
            rec.status = "unknown"
    save_placed_book(book)
    return book


def _same_club(left: str, right: str) -> bool:
    if not left or not right:
        return False
    a, b = fold_name(left), fold_name(right)
    if a == b:
        return True
    if min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)):
        return True
    return _name_score(a, b) >= 0.72


def watch_sides(fixture: Fixture) -> tuple[str | None, str | None]:
    home_name = fixture.home.team if fixture.home else ""
    away_name = fixture.away.team if fixture.away else ""
    if (not home_name or not away_name) and fixture.title:
        parts = split_fixture(fixture.title)
        if parts:
            home_name = home_name or parts[0]
            away_name = away_name or parts[1]
    return matched_watch_club(home_name), matched_watch_club(away_name)


def is_watch_clash(fixture: Fixture) -> bool:
    home, away = watch_sides(fixture)
    return home is not None and away is not None


def watchlist_quote(fixture: Fixture) -> SideQuote | None:
    home_watch, away_watch = watch_sides(fixture)
    if home_watch and not away_watch:
        return fixture.home
    if away_watch and not home_watch:
        return fixture.away
    return None


def opponent_quote(fixture: Fixture) -> SideQuote | None:
    watched = watchlist_quote(fixture)
    if watched is None or fixture.home is None or fixture.away is None:
        return None
    return fixture.away if watched is fixture.home else fixture.home


def _text(market: BinaryMarket) -> str:
    return f"{market.kind} {market.question} {market.slug}".lower()


def _buy_price(book: OutcomeBook | None) -> float | None:
    if book is None:
        return None
    for value in (book.best_ask, book.mid, book.best_bid):
        if value is not None:
            return float(value)
    return None


def _affordable(price: float | None, cfg: KeeperConfig) -> bool:
    if price is None:
        return False
    if price >= cfg.hard_price:
        return False
    return price <= cfg.max_price


_PERIOD_BITS = (
    "1st half",
    "first half",
    "2nd half",
    "second half",
    "1h o/u",
    "2h o/u",
    "1h ou",
    "2h ou",
    "halftime",
    "half-time",
    "half time",
    "1st-half",
    "2nd-half",
)
_TEAM_TOTAL_BITS = ("team total", "team o/u", "team ou", "team totals")
_TEAM_TOTAL_KIND = ("team_total", "first_half", "second_half", "half_total", "period_total")
_FULL_MATCH_OU = re.compile(
    r"^(?:(?:full[ -]?match|match|game|total(?:s)?(?:\s+goals?)?|goals?)\s+)?(?:o/?u|over/?under)\b",
    re.I,
)


def _is_period_or_team_total(market: BinaryMarket) -> bool:
    """True for 1H/2H or single-team O/U books — not full-match goals."""
    kind = (market.kind or "").lower().replace("-", "_")
    text = _text(market)
    if any(bit in kind for bit in _TEAM_TOTAL_KIND):
        return True
    if any(bit in text for bit in _PERIOD_BITS):
        return True
    if any(bit in text for bit in _TEAM_TOTAL_BITS):
        return True
    question = market.question or ""
    if ":" in question:
        clause = question.rsplit(":", 1)[-1].strip()
        if clause and ("o/u" in clause.lower() or "over" in clause.lower() or "under" in clause.lower()):
            if _FULL_MATCH_OU.match(clause) is None:
                return True
    return False


def _totals_market(market: BinaryMarket, line: float) -> bool:
    if _is_period_or_team_total(market):
        return False
    if market.line is not None and abs(market.line - line) > 0.01:
        return False
    text = _text(market)
    if "corner" in text or "exact" in text or "score first" in text or "first to score" in text:
        return False
    if _is_game_totals(market.kind):
        return True
    return "goal" in text or "o/u" in text or "over/under" in text or "total" in text


def is_over_goals(market: BinaryMarket, line: float) -> bool:
    """Full-match goal O/U only. Never 1st-half or team totals."""
    if _is_period_or_team_total(market):
        return False
    if _totals_market(market, line):
        return True
    if market.line is not None and abs(market.line - line) <= 0.01:
        text = _text(market)
        return "goal" in text and "corner" not in text
    return False


def is_neither_first_to_score(market: BinaryMarket) -> bool:
    text = _text(market)
    if "neither" not in text:
        return False
    return "score first" in text or "first to score" in text or "first-to-score" in text


def is_exact_score_00(market: BinaryMarket) -> bool:
    text = _text(market)
    has_zero = "0-0" in text or "0 - 0" in text or "0:0" in text or "0 : 0" in text
    return has_zero and ("exact" in text or "correct score" in text or "0-0" in text)


def is_any_other_score(market: BinaryMarket) -> bool:
    text = _text(market)
    return "any other" in text or "all else" in text or "any other score" in text


def is_corners_over(market: BinaryMarket, line: float) -> bool:
    if _is_period_or_team_total(market):
        return False
    text = _text(market)
    if "corner" not in text:
        return False
    if market.line is not None:
        return abs(market.line - line) <= 0.01
    return str(line) in text or str(line).replace(".", ",") in text


def _outcome(market: BinaryMarket, label: str) -> OutcomeBook | None:
    return market.outcome(label)


def _candidate(market: BinaryMarket, label: str) -> tuple[BinaryMarket, str, float] | None:
    if not market.accepting_orders:
        return None
    book = _outcome(market, label)
    price = _buy_price(book)
    if price is None:
        return None
    return market, label, price


def _pick_cheapest(
    options: list[tuple[BinaryMarket, str, float]],
    cfg: KeeperConfig,
) -> tuple[BinaryMarket, str, float] | None:
    affordable = [row for row in options if _affordable(row[2], cfg)]
    if not affordable:
        return None
    affordable.sort(key=lambda row: (row[2], row[0].question))
    return affordable[0]


def _form_wins(pulse: TeamPulse | None) -> int | None:
    if pulse is None:
        return None
    letters = (pulse.form or "").replace(",", "").replace(" ", "").upper()
    if not letters:
        return None
    return letters.count("W")


def _pulse_for(briefing: Briefing | None, team: str) -> TeamPulse | None:
    if briefing is None or not team:
        return None
    home_names = [briefing.home_name]
    away_names = [briefing.away_name]
    if briefing.home:
        home_names.append(briefing.home.name)
    if briefing.away:
        away_names.append(briefing.away.name)
    if any(_same_club(team, name) for name in home_names if name):
        return briefing.home
    if any(_same_club(team, name) for name in away_names if name):
        return briefing.away
    return None


def _parse_score_line(line: str, pattern: re.Pattern[str]) -> tuple[str, int, int, str] | None:
    match = pattern.match((line or "").strip())
    if not match:
        return None
    return match.group("home"), int(match.group("hs")), int(match.group("as")), match.group("away")


def dog_never_beat(h2h: tuple[str, ...], favorite: str, dog: str) -> bool:
    parsed = 0
    for raw in h2h:
        row = _parse_score_line(raw, _H2H_SCORE)
        if row is None:
            continue
        home, hs, as_, away = row
        fav_in = _same_club(home, favorite) or _same_club(away, favorite)
        dog_in = _same_club(home, dog) or _same_club(away, dog)
        if not (fav_in and dog_in):
            continue
        parsed += 1
        dog_home = _same_club(home, dog)
        if dog_home and hs > as_:
            return False
        if not dog_home and as_ > hs:
            return False
    return parsed > 0


def form_allows_fade(
    briefing: Briefing | None,
    favorite: str,
    dog: str,
    cfg: KeeperConfig,
) -> tuple[bool, str]:
    if briefing is None or briefing.error:
        return False, "skip 1X2: no recent-form briefing"
    fav_pulse = _pulse_for(briefing, favorite)
    dog_pulse = _pulse_for(briefing, dog)
    fav_wins = _form_wins(fav_pulse)
    dog_wins = _form_wins(dog_pulse)
    form_ok = (
        fav_wins is not None
        and dog_wins is not None
        and fav_wins >= cfg.fav_min_wins
        and dog_wins <= cfg.dog_max_wins
    )
    h2h_ok = dog_never_beat(briefing.h2h, favorite, dog)
    if form_ok and h2h_ok:
        return True, f"form {fav_pulse.form if fav_pulse else '?'} vs {dog_pulse.form if dog_pulse else '?'} and H2H clean"
    if form_ok:
        return True, f"form {fav_pulse.form if fav_pulse else '?'} vs {dog_pulse.form if dog_pulse else '?'}"
    if h2h_ok:
        return True, "dog never beat watchlist in H2H"
    return False, "skip 1X2: form and H2H do not support the fade"


def _can_fade_without_form(briefing: Briefing | None, dog_yes: float | None, cfg: KeeperConfig) -> bool:
    if dog_yes is None or dog_yes > cfg.dog_yes_max:
        return False
    if briefing is None or briefing.error:
        return True
    return briefing.home is None and briefing.away is None


def _goals_for_against(team: str, line: str) -> tuple[int, int] | None:
    row = _parse_score_line(line, _LAST_FIVE)
    if row is None:
        return None
    home, hs, as_, away = row
    if _same_club(home, team):
        return hs, as_
    if _same_club(away, team):
        return as_, hs
    return None


def _recent_score_rows(name: str, pulse: TeamPulse) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    seen: set[str] = set()
    labels = [item for item in (name, pulse.name) if item]
    for line in pulse.last_five:
        if line in seen:
            continue
        for label in labels:
            pair = _goals_for_against(label, line)
            if pair is not None:
                seen.add(line)
                rows.append(pair)
                break
    return rows


def is_prolific(name: str, pulse: TeamPulse | None, cfg: KeeperConfig) -> bool:
    """True when recent matches show high scoring — never a club-name list."""
    if pulse is None:
        return False
    rows = list(pulse.last_five_scores) or _recent_score_rows(name, pulse)
    if rows:
        avg_gf = sum(gf for gf, _ in rows) / len(rows)
        avg_total = sum(gf + ga for gf, ga in rows) / len(rows)
        return avg_gf >= cfg.prolific_gf_pg or avg_total >= cfg.prolific_match_goals
    return pulse.gf_pg is not None and pulse.gf_pg >= cfg.prolific_gf_pg


def _goals_for(team: str, lines: tuple[str, ...]) -> list[int]:
    scored: list[int] = []
    for raw in lines:
        pair = _goals_for_against(team, raw)
        if pair is not None:
            scored.append(pair[0])
    return scored


def watchlist_looks_goal_shy(
    briefing: Briefing | None,
    watch_team: str,
    cfg: KeeperConfig,
) -> bool:
    pulse = _pulse_for(briefing, watch_team)
    if pulse is not None and pulse.gf_pg is not None and pulse.gf_pg < cfg.shy_gf_pg:
        return True
    if pulse is not None:
        scored = [gf for gf, _ in pulse.last_five_scores] or _goals_for(watch_team, pulse.last_five)
        if scored and all(item == 0 for item in scored):
            return True
    return False


def _ticket(
    fixture: Fixture,
    rule_id: str,
    market: BinaryMarket,
    outcome_label: str,
    price: float,
    reason: str,
    cfg: KeeperConfig,
) -> Ticket | None:
    book = market.outcome(outcome_label)
    if book is None or not market.accepting_orders:
        return None
    if not _affordable(price, cfg):
        return None
    shares = cfg.shares
    if shares <= 0:
        return None
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
        cost_usd=round(shares * price, 2),
        spread=book.spread,
        reason=reason,
        meaning=_MEANING.get(rule_id, market.question),
        dog_team=dog.team if dog else "",
        favorite_team=fav.team if fav else "",
        dog_yes=dog.yes.mid if dog else None,
        favorite_yes=fav.yes.mid if fav else None,
        kickoff=fixture.kickoff,
    )


def _fade_ticket(fixture: Fixture, cfg: KeeperConfig, briefing: Briefing | None) -> Ticket | None:
    if is_watch_clash(fixture):
        return None
    watched = watchlist_quote(fixture)
    opponent = opponent_quote(fixture)
    if watched is None or opponent is None or watched.yes.mid is None:
        return None
    watch_is_home = watched is fixture.home
    needed = cfg.home_favorite_min if watch_is_home else cfg.away_favorite_min
    if watched.yes.mid < needed:
        return None
    ok, why = form_allows_fade(briefing, watched.team, opponent.team, cfg)
    if not ok:
        if not _can_fade_without_form(briefing, opponent.yes.mid, cfg):
            return None
        why = "no form feed; dog is a mismatch fade"
    market = BinaryMarket(
        question=f"Will {opponent.team} win?",
        slug=fixture.slug,
        kind="moneyline",
        line=None,
        outcomes=(opponent.yes, opponent.no),
        accepting_orders=True,
    )
    price = _buy_price(opponent.no)
    if price is None:
        return None
    venue = "home" if watch_is_home else "away"
    reason = (
        f"{watched.team} {venue} {watched.yes.mid:.3f} (>= {needed:.2f}); "
        f"buy {opponent.team} No; {why}"
    )
    return _ticket(fixture, "keeper_fade_dog", market, "No", price, reason, cfg)


def _over_ticket(fixture: Fixture, cfg: KeeperConfig, briefing: Briefing | None) -> Ticket | None:
    watched = watchlist_quote(fixture)
    if watched is None and not is_watch_clash(fixture):
        return None
    if is_watch_clash(fixture):
        home_name = fixture.home.team if fixture.home else ""
        away_name = fixture.away.team if fixture.away else ""
        if watchlist_looks_goal_shy(briefing, home_name, cfg) and watchlist_looks_goal_shy(
            briefing, away_name, cfg
        ):
            return None
    elif watched is not None and watchlist_looks_goal_shy(briefing, watched.team, cfg):
        return None
    options: list[tuple[BinaryMarket, str, float]] = []
    for market in fixture.extras:
        if is_over_goals(market, cfg.over_line):
            picked = _candidate(market, "Over")
            if picked:
                options.append(picked)
        if is_neither_first_to_score(market):
            picked = _candidate(market, "No")
            if picked:
                options.append(picked)
        if is_exact_score_00(market):
            picked = _candidate(market, "No")
            if picked:
                options.append(picked)
    cheapest = _pick_cheapest(options, cfg)
    if cheapest:
        market, label, price = cheapest
        reason = f"cheapest Over 0.5 equivalent @ {price:.3f} ({market.question})"
        return _ticket(fixture, "keeper_over_0_5", market, label, price, reason, cfg)

    if options and all(row[2] >= cfg.hard_price for row in options):
        for market in fixture.extras:
            if not is_over_goals(market, cfg.over_fallback_line):
                continue
            picked = _candidate(market, "Over")
            if picked is None or not _affordable(picked[2], cfg):
                continue
            reason = f"Over 0.5 books >= {cfg.hard_price:.2f}; fallback Over {cfg.over_fallback_line}"
            return _ticket(fixture, "keeper_over_1_5", picked[0], "Over", picked[2], reason, cfg)
    return None


def _under_ticket(fixture: Fixture, cfg: KeeperConfig, briefing: Briefing | None) -> Ticket | None:
    if is_watch_clash(fixture):
        return None
    fav = fixture.favorite
    if fav is None or fixture.away is None or fav is not fixture.away:
        return None
    home_name = fixture.home.team if fixture.home else ""
    away_name = fixture.away.team if fixture.away else ""
    home_pulse = _pulse_for(briefing, home_name)
    away_pulse = _pulse_for(briefing, away_name)
    if home_pulse is None or away_pulse is None:
        return None
    if is_prolific(home_name, home_pulse, cfg) or is_prolific(away_name, away_pulse, cfg):
        return None

    under_opt: tuple[BinaryMarket, str, float] | None = None
    else_opt: tuple[BinaryMarket, str, float] | None = None
    for market in fixture.extras:
        if is_over_goals(market, cfg.under_line):
            picked = _candidate(market, "Under")
            if picked:
                under_opt = picked
        if is_any_other_score(market):
            picked = _candidate(market, "No")
            if picked:
                else_opt = picked

    if under_opt and _affordable(under_opt[2], cfg):
        market, label, price = under_opt
        reason = f"favorite away; Under {cfg.under_line} @ {price:.3f}"
        return _ticket(fixture, "keeper_under_5_5", market, label, price, reason, cfg)
    if under_opt and under_opt[2] >= cfg.hard_price and else_opt and _affordable(else_opt[2], cfg):
        market, label, price = else_opt
        reason = f"Under {cfg.under_line} >= {cfg.hard_price:.2f}; Exact Score Any Other No @ {price:.3f}"
        return _ticket(fixture, "keeper_exact_else_no", market, label, price, reason, cfg)
    return None


def _corners_ticket(fixture: Fixture, cfg: KeeperConfig) -> Ticket | None:
    if not is_watch_clash(fixture):
        return None
    for market in fixture.extras:
        if not is_corners_over(market, cfg.corner_line):
            continue
        picked = _candidate(market, "Over")
        if picked is None:
            continue
        market, label, price = picked
        if price < cfg.corner_min or price > cfg.corner_max:
            continue
        if not _affordable(price, cfg):
            continue
        reason = f"two watchlist clubs; Over {cfg.corner_line} corners @ {price:.3f}"
        return _ticket(fixture, "keeper_corners_7_5", market, label, price, reason, cfg)
    return None


def propose_keeper_tickets(
    fixture: Fixture,
    cfg: KeeperConfig,
    briefing: Briefing | None = None,
    now: datetime | None = None,
) -> list[Ticket]:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not is_upcoming(fixture, now):
        return []
    home_watch, away_watch = watch_sides(fixture)
    if home_watch is None and away_watch is None:
        return []

    tickets: list[Ticket] = []
    for built in (
        _fade_ticket(fixture, cfg, briefing),
        _over_ticket(fixture, cfg, briefing),
        _under_ticket(fixture, cfg, briefing),
        _corners_ticket(fixture, cfg) if cfg.corners else None,
    ):
        if built:
            tickets.append(built)
    return tickets


def cap_keeper_book(tickets: list[Ticket], settings: Settings) -> list[Ticket]:
    kept: list[Ticket] = []
    spent = 0.0
    for ticket in tickets:
        if spent + ticket.cost_usd > settings.max_open_usd:
            continue
        if spent + ticket.cost_usd > settings.bankroll_usd:
            continue
        kept.append(ticket)
        spent += ticket.cost_usd
    return kept


def _briefing_for(fixture: Fixture) -> Briefing:
    fav = watchlist_quote(fixture)
    favorite_team = fav.team if fav else (fixture.favorite.team if fixture.favorite else "")
    return load_briefing(fixture.league, fixture.title, fixture.kickoff, favorite_team)


def collect_keeper(
    live: bool = False,
    on_progress: ProgressFn | None = None,
    settings: Settings | None = None,
    cfg: KeeperConfig | None = None,
    now: datetime | None = None,
    briefing_fn: Callable[[Fixture], Briefing | None] | None = None,
    league_keys: set[str] | None = None,
    horizon_days: int | None = None,
) -> KeeperScan:
    settings = settings or load_settings()
    if live:
        settings = replace(settings, dry_run=False)
    cfg = cfg or load_keeper_config()
    now = now or utcnow()
    client = GammaClient(settings)
    days = settings.max_days_to_kickoff if horizon_days is None else max(1, horizon_days)
    until = now + timedelta(days=days)
    tickets: list[Ticket] = []
    titles: list[str] = []
    watched = 0
    loader = briefing_fn if briefing_fn is not None else _briefing_for

    for league in settings.leagues:
        if league_keys is not None and league.key not in league_keys:
            continue
        if on_progress:
            on_progress(f"Loading {league.name}…")
        events = client.list_moneyline_events(league, order="startTime", until=until)
        for event in events:
            fixture = client.parse_moneyline(league, event)
            if not is_upcoming(fixture, now):
                continue
            home_watch, away_watch = watch_sides(fixture)
            if home_watch is None and away_watch is None:
                continue
            watched += 1
            titles.append(fixture.title)
            if on_progress:
                on_progress(f"Watchlist: {fixture.title}")
            fixture = client.attach_harvest_books(fixture)
            briefing = loader(fixture)
            if on_progress and briefing is not None and briefing.error:
                on_progress(f"briefing {fixture.title}: {briefing.error}")
            tickets.extend(propose_keeper_tickets(fixture, cfg, briefing=briefing, now=now))

    tickets.sort(key=lambda row: (row.kickoff or datetime.max.replace(tzinfo=timezone.utc), row.fixture, row.rule_id))
    return KeeperScan(
        settings=settings,
        cfg=cfg,
        tickets=cap_keeper_book(tickets, settings),
        watched=watched,
        fixtures=tuple(titles),
        as_of=now,
    )


def place_keeper_book(
    tickets: list[Ticket],
    settings: Settings,
    *,
    live: bool = False,
    on_progress: ProgressFn | None = None,
) -> dict[str, object]:
    """GTC buys. Dry-run unless live=True. Skips tickets already in placed.json."""
    settings = replace(settings, dry_run=not live)
    if settings.dry_run:
        return {"mode": "dry_run", "placed": [], "skipped": [], "errors": [], "fatal": None}

    already = load_placed_book()
    placed: list[dict[str, str]] = []
    skipped: list[str] = []
    errors: list[str] = []
    for ticket in tickets:
        key = ticket_key(ticket)
        rec = already.get(key)
        if rec is not None and rec.blocks_rebuy:
            skipped.append(key)
            if on_progress:
                on_progress(f"skip already placed {key}")
            continue
        try:
            raw = place_ticket(ticket, settings)
            record = placed_order_from_ticket(ticket, raw)
            already[key] = record
            save_placed_book(already)
            row = {
                "key": key,
                "rule": ticket.rule_id,
                "fixture": ticket.fixture,
                "status": record.status,
                "order_id": record.order_id,
                "result": str(raw)[:300],
            }
            placed.append(row)
            if on_progress:
                state = "filled" if record.filled else "working"
                on_progress(f"{state} {ticket.rule_id} {ticket.fixture}")
        except LiveTradingDisabled as exc:
            return {
                "mode": "live",
                "placed": placed,
                "skipped": skipped,
                "errors": errors,
                "fatal": str(exc),
            }
        except Exception as exc:  # noqa: BLE001 — surface venue errors, keep going
            errors.append(f"{ticket.fixture} {ticket.rule_id}: {exc}")
            if on_progress:
                on_progress(f"order failed {ticket.fixture} {ticket.rule_id}: {exc}")
    return {"mode": "live", "placed": placed, "skipped": skipped, "errors": errors, "fatal": None}


def run_once(live: bool = False) -> int:
    result = collect_keeper(live=live)
    settings, cfg, booked, watched = result.settings, result.cfg, open_tickets(result.tickets), result.watched
    print(
        f"watchlist keeper  shares {cfg.shares:g}  "
        f"mode {'LIVE' if not settings.dry_run else 'DRY-RUN'}  "
        f"fixtures {watched}  tickets {len(booked)}",
        flush=True,
    )
    if not booked:
        print("No watchlist keeper tickets in the current window.")
        return 0

    print(f"{'league':<8} {'rule':<22} {'px':>6} {'sh':>5} {'usd':>7}  fixture")
    for ticket in booked:
        print(
            f"{ticket.league:<8} {ticket.rule_id:<22} "
            f"{ticket.price:6.3f} {ticket.shares:5.1f} {ticket.cost_usd:7.2f}  "
            f"{ticket.fixture} [{ticket.outcome}]"
        )
        print(f"{'':8} {ticket.reason}")

    total = sum(t.cost_usd for t in booked)
    print()
    print(f"planned notional ${total:.2f}")

    out = place_keeper_book(booked, settings, live=live)
    if out["mode"] == "dry_run":
        return 0
    for key in out["skipped"]:
        print(f"skip already placed {key}")
    for row in out["placed"]:
        print(json.dumps({"placed": row["rule"], "fixture": row["fixture"], "result": row["result"]}))
    if out["fatal"]:
        print(f"live trading disabled: {out['fatal']}", file=sys.stderr)
        return 2
    for line in out["errors"]:
        print(f"order failed {line}", file=sys.stderr)
    return 1 if out["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Watchlist keeper: ~5-share form-checked buys (default dry-run)"
    )
    parser.add_argument("--live", action="store_true", help="Place GTC bids (requires .env keys)")
    parser.add_argument("--loop", action="store_true", help="Repeat on poll_seconds")
    args = parser.parse_args(argv)

    if args.loop:
        import time

        settings = load_settings()
        while True:
            code = run_once(live=args.live)
            if code == 2:
                return code
            time.sleep(settings.poll_seconds)
    return run_once(live=args.live)


if __name__ == "__main__":
    raise SystemExit(main())
