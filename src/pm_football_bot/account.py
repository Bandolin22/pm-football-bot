from __future__ import annotations

import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from pm_football_bot.board import WATCH_QUERIES, matched_watch_club, watch_display_name
from pm_football_bot.swisstony import DATA_API, league_of, parent_slug
from pm_football_bot.tape import classify_kind

DEFAULT_USERNAME = "zerobetap"
DEFAULT_WALLET = "0x0cfeece79f89fbc92d8115edfab11ff6af847290"
PROFILE = f"https://polymarket.com/@{DEFAULT_USERNAME}"

FACTOR_ORDER = ("dog_no", "over_05", "under_55", "exact_no", "corner", "other")
FACTOR_LABEL = {
    "dog_no": "1X2 dog No",
    "over_05": "Over 0.5",
    "under_55": "Under 5.5",
    "exact_no": "Exact score No",
    "corner": "Corners",
    "other": "Other",
}

_VS = re.compile(r"^(?:will\s+)?(?P<home>.+?)\s+vs\.?\s+(?P<away>.+?)(?:\s*[-:]|\s+end in a draw|\s*$)", re.I)
_WIN = re.compile(r"^will\s+(.+?)\s+win on\s+", re.I)
_NON_SOCCER = re.compile(
    r"\b(bitcoin|btc|ethereum|eth\b|solana|up or down|fed rate|trump|weather)\b",
    re.I,
)
_FOOTBALL_HINT = (
    "win on",
    "exact score",
    "correct score",
    "corner",
    "both teams to score",
    "end in a draw",
    "o/u",
    " vs.",
    " vs ",
)


def profile_url(username: str) -> str:
    return f"https://polymarket.com/@{username.lstrip('@')}"


def classify_factor(title: str, outcome: str, avg_price: float | None = None) -> str:
    """Map a fill/position onto the harvest-style buckets the desk tracks."""
    low = (title or "").lower()
    side = (outcome or "").strip().lower()
    if "corner" in low:
        return "corner"
    if "exact score" in low or "correct score" in low:
        return "exact_no" if side == "no" else "other"
    kind = classify_kind(title, outcome)
    if kind.family == "game_ou" and kind.side == "over" and kind.line == 0.5:
        return "over_05"
    if kind.family == "game_ou" and kind.side == "under" and kind.line == 5.5:
        return "under_55"
    if kind.family == "moneyline" and kind.side == "no":
        if avg_price is None or avg_price >= 0.70:
            return "dog_no"
        return "other"
    return "other"


def parse_teams(title: str) -> tuple[str, ...]:
    text = (title or "").strip()
    win = _WIN.match(text)
    if win:
        return (win.group(1).strip(),)
    match = _VS.match(text)
    if not match:
        return ()
    home = match.group("home").strip()
    away = match.group("away").strip()
    away = re.split(r"\s+[-:]", away, maxsplit=1)[0].strip()
    if home.lower().startswith("will "):
        home = home[5:].strip()
    if home and away and "win on" not in home.lower():
        return (home, away)
    return ()


def is_soccer(title: str, slug: str) -> bool:
    slug_l = (slug or "").lower()
    if league_of(slug_l) or league_of(parent_slug(slug_l)):
        return True
    low = (title or "").lower()
    if _NON_SOCCER.search(low):
        return False
    return any(hint in low for hint in _FOOTBALL_HINT)


def watch_clubs_for(teams: tuple[str, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for name in teams:
        label = matched_watch_club(name)
        if label and label not in found:
            found.append(label)
    return tuple(found)


@dataclass
class AccountLot:
    title: str
    outcome: str
    event_slug: str
    avg_price: float
    shares: float
    cost_usd: float
    mark_usd: float
    pnl: float
    cur_price: float | None
    end_date: str | None
    status: str
    factor: str
    teams: tuple[str, ...]
    watch_clubs: tuple[str, ...]
    soccer: bool
    timestamp: datetime | None = None

    @property
    def cents(self) -> float:
        return round(self.avg_price * 100, 1)


@dataclass
class AccountFill:
    utc: datetime
    side: str
    title: str
    outcome: str
    shares: float
    price: float
    usd: float
    event_slug: str
    factor: str
    teams: tuple[str, ...]
    soccer: bool
    tx: str


@dataclass
class Bucket:
    key: str
    label: str
    lots: int = 0
    cost: float = 0.0
    mark: float = 0.0
    realized: float = 0.0
    unrealized: float = 0.0

    @property
    def pnl(self) -> float:
        return round(self.realized + self.unrealized, 4)


@dataclass
class AccountBook:
    username: str
    wallet: str
    positions_value: float
    lots: list[AccountLot] = field(default_factory=list)
    fills: list[AccountFill] = field(default_factory=list)

    @property
    def profile(self) -> str:
        return profile_url(self.username)


def resolve_username(name: str, session: requests.Session | None = None) -> tuple[str, str]:
    raw = (name or "").strip().lstrip("@")
    if raw.lower().startswith("0x") and len(raw) >= 42:
        return raw, raw.lower()
    if raw.lower() == DEFAULT_USERNAME:
        return DEFAULT_USERNAME, DEFAULT_WALLET
    client = session or requests.Session()
    response = client.get(
        f"{DATA_API}/v1/leaderboard",
        params={"userName": raw},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    rows = data if isinstance(data, list) else data.get("data") or data.get("leaderboard") or []
    for row in rows:
        wallet = str(row.get("proxyWallet") or "")
        user = str(row.get("userName") or raw)
        if wallet:
            return user, wallet.lower()
    raise RuntimeError(f"Could not resolve Polymarket profile @{raw}")


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("positions", "trades", "data", "closedPositions"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _fetch_page(
    path: str,
    params: dict[str, Any],
    *,
    session: requests.Session | None,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    client = session or requests.Session()
    response = client.get(f"{DATA_API}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return _as_list(response.json())


def _paginate(
    path: str,
    params: dict[str, Any],
    *,
    session: requests.Session,
    page_size: int = 100,
    max_rows: int = 12000,
    parallel: bool = False,
) -> list[dict[str, Any]]:
    first = _fetch_page(
        path,
        {**params, "limit": page_size, "offset": 0},
        session=session,
    )
    if not first:
        return []
    if len(first) < page_size or not parallel:
        rows = list(first)
        offset = len(first)
        while first and len(first) >= page_size and offset < max_rows:
            first = _fetch_page(
                path,
                {**params, "limit": page_size, "offset": offset},
                session=session,
            )
            if not first:
                break
            rows.extend(first)
            if len(first) < page_size:
                break
            offset += len(first)
        return rows

    rows = list(first)
    offset = page_size
    workers = 8
    while offset < max_rows:
        batch = [offset + index * page_size for index in range(workers)]
        found_short = False
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _fetch_page,
                    path,
                    {**params, "limit": page_size, "offset": item},
                    session=None,
                ): item
                for item in batch
            }
            pages = {futures[future]: future.result() for future in as_completed(futures)}
        for item in batch:
            page = pages.get(item) or []
            if not page:
                found_short = True
                break
            rows.extend(page)
            if len(page) < page_size:
                found_short = True
                break
        if found_short:
            break
        offset += workers * page_size
    return rows[:max_rows]


def _ts(raw: Any) -> datetime | None:
    if raw in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _lot_from_raw(raw: dict[str, Any], status: str) -> AccountLot | None:
    title = str(raw.get("title") or "")
    outcome = str(raw.get("outcome") or "")
    slug = str(raw.get("eventSlug") or raw.get("slug") or "")
    avg = float(raw.get("avgPrice") or 0)
    bought = float(raw.get("totalBought") or raw.get("size") or 0)
    size = float(raw.get("size") or bought)
    cost = float(raw.get("initialValue") or 0) or round(bought * avg, 4)
    if bought <= 0 and size <= 0:
        return None
    if status == "closed":
        pnl = float(raw.get("realizedPnl") or 0)
        mark = 0.0
        shares = bought
    else:
        pnl = float(raw.get("cashPnl") or 0)
        mark = float(raw.get("currentValue") or 0)
        shares = size if size > 0 else bought
    cur = raw.get("curPrice")
    teams = parse_teams(title)
    return AccountLot(
        title=title,
        outcome=outcome,
        event_slug=slug,
        avg_price=avg,
        shares=shares,
        cost_usd=round(cost, 4),
        mark_usd=round(mark, 4),
        pnl=round(pnl, 4),
        cur_price=float(cur) if cur is not None else None,
        end_date=str(raw.get("endDate") or "") or None,
        status=status,
        factor=classify_factor(title, outcome, avg),
        teams=teams,
        watch_clubs=watch_clubs_for(teams),
        soccer=is_soccer(title, slug),
        timestamp=_ts(raw.get("timestamp")),
    )


def _fill_from_raw(raw: dict[str, Any]) -> AccountFill | None:
    size = float(raw.get("size") or 0)
    price = float(raw.get("price") or 0)
    ts = _ts(raw.get("timestamp"))
    if size <= 0 or price <= 0 or ts is None:
        return None
    title = str(raw.get("title") or "")
    outcome = str(raw.get("outcome") or "")
    slug = str(raw.get("eventSlug") or raw.get("slug") or "")
    return AccountFill(
        utc=ts,
        side=str(raw.get("side") or "BUY").upper(),
        title=title,
        outcome=outcome,
        shares=size,
        price=price,
        usd=round(size * price, 4),
        event_slug=slug,
        factor=classify_factor(title, outcome, price),
        teams=parse_teams(title),
        soccer=is_soccer(title, slug),
        tx=str(raw.get("transactionHash") or ""),
    )


def _enrich_teams(lots: list[AccountLot]) -> None:
    by_parent: dict[str, list[AccountLot]] = defaultdict(list)
    for lot in lots:
        by_parent[parent_slug(lot.event_slug)].append(lot)
    parent_teams: dict[str, tuple[str, ...]] = {}
    for parent, group in by_parent.items():
        for lot in group:
            if len(lot.teams) >= 2:
                parent_teams[parent] = lot.teams
                break
    for lot in lots:
        inherited = parent_teams.get(parent_slug(lot.event_slug))
        if not inherited:
            continue
        merged: list[str] = list(lot.teams)
        for name in inherited:
            if name not in merged:
                merged.append(name)
        lot.teams = tuple(merged)
        lot.watch_clubs = watch_clubs_for(lot.teams)


def _dedupe_lots(closed: list[AccountLot], opened: list[AccountLot]) -> list[AccountLot]:
    closed_keys = {(lot.event_slug, lot.title, lot.outcome) for lot in closed}
    rows = list(closed)
    for lot in opened:
        key = (lot.event_slug, lot.title, lot.outcome)
        if key in closed_keys:
            continue
        # Redeemed-but-uncollected losers still sit on /positions at 0.
        if lot.mark_usd < 0.01 and lot.cur_price == 0:
            lot.status = "closed"
        rows.append(lot)
    return rows


def fetch_value(wallet: str, session: requests.Session) -> float:
    response = session.get(f"{DATA_API}/value", params={"user": wallet}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list) and payload:
        return float(payload[0].get("value") or 0)
    if isinstance(payload, dict):
        return float(payload.get("value") or 0)
    return 0.0


def load_account(
    username: str = DEFAULT_USERNAME,
    *,
    fill_limit: int = 250,
    session: requests.Session | None = None,
) -> AccountBook:
    client = session or requests.Session()
    user, wallet = resolve_username(username, client)
    positions_value = fetch_value(wallet, client)
    closed_raw = _paginate(
        "/closed-positions",
        {"user": wallet},
        session=client,
        page_size=50,
        max_rows=12000,
        parallel=True,
    )
    open_raw = _paginate(
        "/positions",
        {"user": wallet, "sizeThreshold": 0},
        session=client,
        page_size=500,
        max_rows=8000,
    )
    trade_raw = _paginate(
        "/trades",
        {"user": wallet},
        session=client,
        page_size=min(fill_limit, 500),
        max_rows=fill_limit,
    )
    closed = [lot for row in closed_raw if (lot := _lot_from_raw(row, "closed"))]
    opened = [lot for row in open_raw if (lot := _lot_from_raw(row, "open"))]
    lots = _dedupe_lots(closed, opened)
    _enrich_teams(lots)
    fills = [fill for row in trade_raw if (fill := _fill_from_raw(row))]
    fills.sort(key=lambda row: row.utc, reverse=True)
    lots.sort(key=lambda row: (row.status != "open", -(row.timestamp.timestamp() if row.timestamp else 0)))
    return AccountBook(
        username=user,
        wallet=wallet,
        positions_value=round(positions_value, 4),
        lots=lots,
        fills=fills,
    )


def _selected(lots: list[AccountLot], soccer_only: bool) -> list[AccountLot]:
    if not soccer_only:
        return lots
    return [lot for lot in lots if lot.soccer]


def summarize_factors(lots: list[AccountLot], soccer_only: bool = True) -> list[Bucket]:
    buckets = {key: Bucket(key, FACTOR_LABEL[key]) for key in FACTOR_ORDER}
    for lot in _selected(lots, soccer_only):
        bucket = buckets.get(lot.factor) or buckets["other"]
        bucket.lots += 1
        bucket.cost += lot.cost_usd
        bucket.mark += lot.mark_usd
        if lot.status == "open":
            bucket.unrealized += lot.pnl
        else:
            bucket.realized += lot.pnl
    return [buckets[key] for key in FACTOR_ORDER]


def summarize_teams(
    lots: list[AccountLot],
    *,
    soccer_only: bool = True,
    watchlist_only: bool = False,
) -> list[Bucket]:
    buckets: dict[str, Bucket] = {}
    for lot in _selected(lots, soccer_only):
        if watchlist_only:
            names = lot.watch_clubs
        else:
            names = list(lot.watch_clubs)
            for name in lot.teams:
                if matched_watch_club(name) is None and name not in names:
                    names.append(name)
            names = tuple(names)
        if not names:
            continue
        for name in names:
            bucket = buckets.get(name)
            if bucket is None:
                bucket = Bucket(name, watch_display_name(name))
                buckets[name] = bucket
            bucket.lots += 1
            bucket.cost += lot.cost_usd
            bucket.mark += lot.mark_usd
            if lot.status == "open":
                bucket.unrealized += lot.pnl
            else:
                bucket.realized += lot.pnl
    watch_rank = {name: index for index, name in enumerate(WATCH_QUERIES)}
    return sorted(
        buckets.values(),
        key=lambda row: (-row.pnl, watch_rank.get(row.key, 99), row.label.lower()),
    )


def totals(lots: list[AccountLot], soccer_only: bool = True) -> dict[str, float]:
    rows = _selected(lots, soccer_only)
    realized = sum(lot.pnl for lot in rows if lot.status != "open")
    unrealized = sum(lot.pnl for lot in rows if lot.status == "open")
    open_rows = [lot for lot in rows if lot.status == "open"]
    return {
        "lots": float(len(rows)),
        "open_lots": float(len(open_rows)),
        "cost": round(sum(lot.cost_usd for lot in rows), 4),
        "open_cost": round(sum(lot.cost_usd for lot in open_rows), 4),
        "mark": round(sum(lot.mark_usd for lot in open_rows), 4),
        "realized": round(realized, 4),
        "unrealized": round(unrealized, 4),
        "pnl": round(realized + unrealized, 4),
        "biggest_win": round(max((lot.pnl for lot in rows), default=0.0), 4),
    }
