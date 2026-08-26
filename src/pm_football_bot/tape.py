from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from pm_football_bot.gamma import _parse_kickoff
from pm_football_bot.simulate import same_team
from pm_football_bot.swisstony import DATA_API, SWISSTONY, league_of, parent_slug

GAMMA = "https://gamma-api.polymarket.com"
_WIN = re.compile(r"^will\s+(.+?)\s+win on\s+", re.I)
_OU = re.compile(r"o/u\s*(\d+(?:\.\d+)?)", re.I)

EDGE_ORDER = ("harvest", "gap", "flip", "clock", "tail", "pair", "other")
EDGE_LABEL = {
    "harvest": "Harvest",
    "gap": "Gap",
    "flip": "Flip",
    "clock": "Clock",
    "tail": "Tail",
    "pair": "Pair",
    "other": "Other",
}


def _ts(raw: Any) -> datetime:
    return datetime.fromtimestamp(int(raw), tz=timezone.utc)


def _norm_outcome(outcome: str) -> str:
    label = (outcome or "").strip().lower()
    if label in {"yes", "over"}:
        return label if label == "over" else "yes"
    if label in {"no", "under"}:
        return label if label == "under" else "no"
    return label


@dataclass(frozen=True)
class FillKind:
    family: str
    side: str
    team: str | None = None
    line: float | None = None
    half: bool = False

    @property
    def label(self) -> str:
        bits = [self.family]
        if self.team:
            bits.append(self.team.split()[0][:12])
        if self.line is not None:
            bits.append(str(self.line))
        bits.append(self.side)
        if self.half:
            bits.append("1H")
        return " ".join(bits)


def classify_kind(title: str, outcome: str) -> FillKind:
    text = (title or "").strip()
    low = text.lower()
    side = _norm_outcome(outcome)
    half = "1st half" in low or "first half" in low

    if "both teams to score" in low:
        return FillKind("btts", side if side in {"yes", "no"} else "other", half=half)
    if "end in a draw" in low:
        return FillKind("draw", side if side in {"yes", "no"} else "other", half=half)
    win = _WIN.match(text)
    if win:
        return FillKind("moneyline", side if side in {"yes", "no"} else "other", team=win.group(1).strip(), half=half)
    if low.startswith("spread:"):
        return FillKind("spread", side if side else "other", half=half)
    if "o/u" in low:
        match = _OU.search(text)
        line = float(match.group(1)) if match else None
        tail = text.split(":")[-1].strip() if ":" in text else text
        is_game = bool(re.match(r"^(1st half |2nd half )?o/u\b", tail, re.I))
        family = "game_ou" if is_game else "team_ou"
        ou = side if side in {"over", "under"} else ("over" if side == "yes" else "under" if side == "no" else "other")
        return FillKind(family, ou, line=line, half=half)
    return FillKind("other", side or "other", half=half)


@dataclass
class Fill:
    utc: datetime
    side: str
    title: str
    outcome: str
    shares: float
    price: float
    usd: float
    event_slug: str
    condition_id: str
    tx: str
    kind: FillKind
    live: bool = False
    edge: str = "other"
    paired: bool = False

    @property
    def cents(self) -> float:
        return round(self.price * 100, 1)

    @property
    def window(self) -> str:
        return "in-play" if self.live else "pre-match"


def fill_from_trade(raw: dict[str, Any], kickoff: datetime | None) -> Fill | None:
    if str(raw.get("side") or "BUY").upper() != "BUY":
        return None
    ts = _ts(raw.get("timestamp") or 0)
    size = float(raw.get("size") or 0)
    price = float(raw.get("price") or 0)
    if size <= 0 or price <= 0:
        return None
    title = str(raw.get("title") or "")
    outcome = str(raw.get("outcome") or "")
    live = bool(kickoff and ts >= kickoff)
    return Fill(
        utc=ts,
        side="BUY",
        title=title,
        outcome=outcome,
        shares=size,
        price=price,
        usd=round(size * price, 4),
        event_slug=str(raw.get("eventSlug") or raw.get("slug") or ""),
        condition_id=str(raw.get("conditionId") or ""),
        tx=str(raw.get("transactionHash") or ""),
        kind=classify_kind(title, outcome),
        live=live,
    )


def infer_favorite(fills: list[Fill], home: str | None, away: str | None) -> tuple[str | None, str | None]:
    pre_yes: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for fill in fills:
        if fill.live or fill.kind.family != "moneyline" or fill.kind.side != "yes" or not fill.kind.team:
            continue
        pre_yes[fill.kind.team].append((fill.usd, fill.price))
    vwap: dict[str, float] = {}
    for team, rows in pre_yes.items():
        notional = sum(usd for usd, _ in rows)
        if notional <= 0:
            continue
        vwap[team] = sum(usd * price for usd, price in rows) / notional
    if vwap:
        favorite = max(vwap, key=lambda name: vwap[name])
        dog = min(vwap, key=lambda name: vwap[name]) if len(vwap) > 1 else None
        return favorite, dog
    return home, away


def _is_favorite(team: str | None, favorite: str | None) -> bool:
    return bool(team and favorite and same_team(team, favorite))


def _is_dog(team: str | None, dog: str | None) -> bool:
    return bool(team and dog and same_team(team, dog))


def _is_harvest(fill: Fill, dog: str | None) -> bool:
    if fill.live:
        return False
    price = fill.price
    kind = fill.kind
    if kind.family == "moneyline" and kind.side == "no" and 0.85 <= price <= 0.97:
        return (not dog) or _is_dog(kind.team, dog)
    if kind.family == "game_ou" and kind.side == "over" and kind.line == 0.5 and 0.85 <= price <= 0.97:
        return True
    if kind.family == "game_ou" and kind.side == "under" and kind.line in {4.5, 5.5} and 0.85 <= price <= 0.97:
        return True
    if kind.family == "spread" and price >= 0.95:
        return True
    return False


def _is_gap(fill: Fill, favorite: str | None) -> bool:
    return (
        fill.live
        and fill.kind.family == "moneyline"
        and fill.kind.side == "yes"
        and 0.45 <= fill.price <= 0.72
        and _is_favorite(fill.kind.team, favorite)
    )


def _is_clock(fill: Fill) -> bool:
    if not fill.live:
        return False
    kind = fill.kind
    if kind.family == "draw" and kind.side == "yes":
        return True
    if kind.family == "moneyline" and kind.side == "no" and fill.price >= 0.55:
        return True
    if kind.family == "game_ou" and kind.side == "under" and kind.line is not None and kind.line <= 2.5:
        return True
    return False


def _is_flip(fill: Fill, favorite: str | None) -> bool:
    if not fill.live or fill.price < 0.84:
        return False
    kind = fill.kind
    if kind.family == "moneyline" and kind.side == "yes" and _is_favorite(kind.team, favorite):
        return True
    if kind.family == "draw" and kind.side == "no":
        return True
    return False


def _is_tail(fill: Fill, dog: str | None) -> bool:
    return (
        fill.kind.family == "moneyline"
        and fill.kind.side == "yes"
        and fill.price <= 0.18
        and ((not dog) or _is_dog(fill.kind.team, dog))
    )


def _mark_pairs(fills: list[Fill], window_seconds: int = 120) -> None:
    by_cond: dict[str, list[Fill]] = defaultdict(list)
    for fill in fills:
        if fill.condition_id:
            by_cond[fill.condition_id].append(fill)
    opposite = {"yes": "no", "no": "yes", "over": "under", "under": "over"}
    for group in by_cond.values():
        ordered = sorted(group, key=lambda row: row.utc)
        for i, left in enumerate(ordered):
            want = opposite.get(left.kind.side)
            if not want:
                continue
            for right in ordered[i + 1 :]:
                if (right.utc - left.utc).total_seconds() > window_seconds:
                    break
                if right.kind.side != want:
                    continue
                pair_sum = left.price + right.price
                if 0.88 <= pair_sum <= 1.05:
                    left.paired = True
                    right.paired = True


def tag_fills(fills: list[Fill], favorite: str | None, dog: str | None) -> list[Fill]:
    _mark_pairs(fills)
    for fill in fills:
        harvest = _is_harvest(fill, dog)
        gap = _is_gap(fill, favorite)
        flip = _is_flip(fill, favorite)
        clock = _is_clock(fill)
        tail = _is_tail(fill, dog)
        if harvest:
            fill.edge = "harvest"
        elif gap:
            fill.edge = "gap"
        elif flip:
            fill.edge = "flip"
        elif clock:
            fill.edge = "clock"
        elif tail:
            fill.edge = "tail"
        elif fill.paired:
            fill.edge = "pair"
        else:
            fill.edge = "other"
    return fills


@dataclass
class TapeSummary:
    pre_usd: float
    live_usd: float
    pre_n: int
    live_n: int
    by_edge: dict[str, float]
    by_family_live: dict[str, float]
    by_family_pre: dict[str, float]


def summarize(fills: list[Fill]) -> TapeSummary:
    pre = [row for row in fills if not row.live]
    live = [row for row in fills if row.live]
    by_edge: dict[str, float] = defaultdict(float)
    by_pre: dict[str, float] = defaultdict(float)
    by_live: dict[str, float] = defaultdict(float)
    for row in fills:
        by_edge[row.edge] += row.usd
        bucket = by_live if row.live else by_pre
        bucket[row.kind.family] += row.usd
    return TapeSummary(
        pre_usd=round(sum(row.usd for row in pre), 2),
        live_usd=round(sum(row.usd for row in live), 2),
        pre_n=len(pre),
        live_n=len(live),
        by_edge={key: round(by_edge.get(key, 0.0), 2) for key in EDGE_ORDER if by_edge.get(key)},
        by_family_live={k: round(v, 2) for k, v in sorted(by_live.items(), key=lambda kv: -kv[1])},
        by_family_pre={k: round(v, 2) for k, v in sorted(by_pre.items(), key=lambda kv: -kv[1])},
    )


@dataclass
class MatchTape:
    parent: str
    title: str
    league: str | None
    kickoff: datetime | None
    home: str | None
    away: str | None
    favorite: str | None
    dog: str | None
    score: str | None
    ended: bool
    live_now: bool
    fills: list[Fill]
    summary: TapeSummary
    realized_pnl: float | None = None
    event_url: str = ""


def _event(slug: str, session: requests.Session) -> dict[str, Any] | None:
    response = session.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data.get("slug"):
        return data
    return None


def _teams(event: dict[str, Any]) -> tuple[str | None, str | None]:
    teams = event.get("teams") or []
    home = away = None
    if isinstance(teams, list) and teams:
        ordered = sorted(
            teams,
            key=lambda row: 0 if str(row.get("ordering") or "").lower() == "home" else 1,
        )
        if ordered:
            home = str(ordered[0].get("name") or "") or None
        if len(ordered) > 1:
            away = str(ordered[1].get("name") or "") or None
        return home, away
    title = str(event.get("title") or "")
    match = re.match(r"^(?P<home>.+?)\s+vs\.?\s+(?P<away>.+)$", title, re.I)
    if match:
        return match.group("home").strip(), match.group("away").strip()
    return None, None


def fetch_trades(event_id: str, session: requests.Session, wallet: str = SWISSTONY) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = session.get(
            f"{DATA_API}/trades",
            params={"user": wallet, "eventId": event_id, "limit": 500, "offset": offset},
            timeout=45,
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        if len(page) < 500:
            break
        offset += len(page)
        if offset > 8000:
            break
    return rows


def fetch_closed_pnl(event_id: str, session: requests.Session, wallet: str = SWISSTONY) -> float | None:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = session.get(
            f"{DATA_API}/closed-positions",
            params={"user": wallet, "eventId": event_id, "limit": 100, "offset": offset},
            timeout=30,
        )
        if response.status_code != 200:
            return None if not rows else round(sum(float(p.get("realizedPnl") or 0) for p in rows), 2)
        page = response.json()
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        if len(page) < 100:
            break
        offset += len(page)
        if offset > 2000:
            break
    if not rows:
        return None
    return round(sum(float(p.get("realizedPnl") or 0) for p in rows), 2)


def load_tape(slug: str, wallet: str = SWISSTONY, session: requests.Session | None = None) -> MatchTape:
    client = session or requests.Session()
    parent = parent_slug(slug)
    event = _event(parent, client)
    if event is None:
        raise ValueError(f"No Polymarket event for {parent}")
    more = _event(f"{parent}-more-markets", client)
    kickoff = _parse_kickoff(event)
    raw_trades: list[dict[str, Any]] = fetch_trades(str(event["id"]), client, wallet)
    if more and more.get("id"):
        raw_trades.extend(fetch_trades(str(more["id"]), client, wallet))
    fills = []
    seen: set[tuple] = set()
    for raw in raw_trades:
        key = (
            raw.get("transactionHash"),
            raw.get("asset"),
            raw.get("timestamp"),
            raw.get("size"),
            raw.get("price"),
            raw.get("outcome"),
        )
        if key in seen:
            continue
        seen.add(key)
        fill = fill_from_trade(raw, kickoff)
        if fill:
            fills.append(fill)
    fills.sort(key=lambda row: row.utc)
    home, away = _teams(event)
    favorite, dog = infer_favorite(fills, home, away)
    tag_fills(fills, favorite, dog)
    pnl = None
    if event.get("ended") or event.get("closed"):
        parts = [fetch_closed_pnl(str(event["id"]), client, wallet)]
        if more and more.get("id"):
            parts.append(fetch_closed_pnl(str(more["id"]), client, wallet))
        known = [p for p in parts if p is not None]
        if known:
            pnl = round(sum(known), 2)
    title = str(event.get("title") or parent)
    return MatchTape(
        parent=parent,
        title=title,
        league=league_of(parent),
        kickoff=kickoff,
        home=home,
        away=away,
        favorite=favorite,
        dog=dog,
        score=str(event["score"]) if event.get("score") else None,
        ended=bool(event.get("ended") or event.get("closed")),
        live_now=bool(event.get("live")),
        fills=fills,
        summary=summarize(fills),
        realized_pnl=pnl,
        event_url=f"https://polymarket.com/event/{parent}",
    )


def fixture_options(swiss_parents: list[str], extra: list[str] | None = None) -> list[str]:
    seen: list[str] = []
    for slug in [*swiss_parents, *(extra or [])]:
        key = parent_slug(slug)
        if key and key not in seen:
            seen.append(key)
    return seen
