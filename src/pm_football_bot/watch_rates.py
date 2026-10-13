from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from io import StringIO
from typing import Callable
from urllib.parse import quote

import requests

from pm_football_bot.board import (
    WATCH_CLUBS,
    WATCH_NATIONS,
    matched_watch_club,
    watch_display_name,
)
from pm_football_bot.scout import HTTP_UA, RESULTS_CSV_HOST

NEW_HOST = "https://www.football-data.co.uk/new"
INTL_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

CSV_ALIASES = {
    "flamengo rj": "Flamengo",
    "cr flamengo": "Flamengo",
    "paris sg": "PSG",
    "sp lisbon": "Sporting CP",
    "ath madrid": "Atletic Madrid",
    "bodo glimt": "Bodo Glimt",
    "bodo/glimt": "Bodo Glimt",
}

LEAGUE_FILES = (
    ("E0", "Premier League"),
    ("SP1", "LaLiga"),
    ("D1", "Bundesliga"),
    ("I1", "Serie A"),
    ("F1", "Ligue 1"),
    ("P1", "Primeira Liga"),
    ("B1", "Belgium"),
    ("N1", "Eredivisie"),
    ("SC0", "Scotland"),
    ("T1", "Süper Lig"),
)

EXTRA_FILES = (
    (f"{NEW_HOST}/BRA.csv", "Brasileirão", ("Home", "Away", "HG", "AG", "Date")),
    (f"{NEW_HOST}/NOR.csv", "Eliteserien", ("Home", "Away", "HG", "AG", "Date")),
)

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = "pm-football-bot/0.1 (https://github.com/Bandolin22/pm-football-bot; watchlist-rates)"
_SCORE_CELL = re.compile(r"(\d+)\s*[–—−-]\s*(\d+)")

FetchFn = Callable[[str], str | None]


def season_folders(start: date, end: date) -> tuple[str, ...]:
    """football-data.co.uk mmz4281 keys that can overlap [start, end]."""
    years: set[int] = set()
    for day in (start, end):
        years.add(day.year if day.month >= 7 else day.year - 1)
        years.add((day.year if day.month >= 7 else day.year - 1) - 1)
    return tuple(f"{year % 100:02d}{(year + 1) % 100:02d}" for year in sorted(years))


def overlapping_spl_seasons(start: date, end: date) -> tuple[int, ...]:
    """Saudi Pro League season-start years whose Aug–May window overlaps [start, end]."""
    years: set[int] = set()
    for day in (start, end):
        years.add(day.year if day.month >= 7 else day.year - 1)
        years.add((day.year if day.month >= 7 else day.year - 1) - 1)
    kept: list[int] = []
    for year in sorted(years):
        season_start = date(year, 8, 1)
        season_end = date(year + 1, 5, 31)
        if season_end < start or season_start > end:
            continue
        kept.append(year)
    return tuple(kept)


def spl_wiki_url(year: int) -> str:
    page = f"{year}–{str(year + 1)[2:]}_Saudi_Pro_League"
    return (
        f"{WIKI_API}?action=parse&page={quote(page, safe='_')}&prop=text&format=json"
    )


def spl_overlap_day(year: int, start: date, end: date) -> date | None:
    lo = max(date(year, 8, 1), start)
    hi = min(date(year + 1, 5, 31), end)
    if lo > hi:
        return None
    return hi


def parse_day(raw: str) -> date | None:
    text = (raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def watch_of(name: str) -> str | None:
    folded = " ".join((name or "").lower().replace("/", " ").split())
    if folded in CSV_ALIASES:
        name = CSV_ALIASES[folded]
    return matched_watch_club(name)


def score_flags(hg: int, ag: int) -> tuple[bool, bool, bool]:
    """0–0, Over 5.5 (6+ goals), Over 3–3 (a side scored 4+)."""
    return hg == 0 and ag == 0, hg + ag >= 6, hg > 3 or ag > 3


@dataclass
class RateBucket:
    matches: int = 0
    zero: int = 0
    over55: int = 0
    other: int = 0

    def add(self, hg: int, ag: int) -> None:
        zero, over55, other = score_flags(hg, ag)
        self.matches += 1
        self.zero += int(zero)
        self.over55 += int(over55)
        self.other += int(other)

    def rate(self, count: int) -> float:
        if self.matches <= 0:
            return 0.0
        return round(100 * count / self.matches, 1)

    @property
    def zero_rate(self) -> float:
        return self.rate(self.zero)

    @property
    def over55_rate(self) -> float:
        return self.rate(self.over55)

    @property
    def other_rate(self) -> float:
        return self.rate(self.other)

    def combined(self, other: RateBucket) -> RateBucket:
        out = RateBucket()
        out.matches = self.matches + other.matches
        out.zero = self.zero + other.zero
        out.over55 = self.over55 + other.over55
        out.other = self.other + other.other
        return out

    def as_dict(self) -> dict[str, int | float]:
        return {
            "matches": self.matches,
            "zero": self.zero,
            "over55": self.over55,
            "other": self.other,
            "zero_rate": self.zero_rate,
            "over55_rate": self.over55_rate,
            "other_rate": self.other_rate,
        }


@dataclass
class TeamSplit:
    query: str
    label: str
    kind: str
    home: RateBucket = field(default_factory=RateBucket)
    away: RateBucket = field(default_factory=RateBucket)
    clash: RateBucket = field(default_factory=RateBucket)
    rest: RateBucket = field(default_factory=RateBucket)

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "label": self.label,
            "kind": self.kind,
            "home": self.home.as_dict(),
            "away": self.away.as_dict(),
            "all": self.home.combined(self.away).as_dict(),
            "clash": self.clash.as_dict(),
            "rest": self.rest.as_dict(),
        }


@dataclass
class ClashGame:
    date: str
    home: str
    away: str
    score: str
    zero: bool
    over55: bool
    other: bool
    kind: str

    def flag(self) -> str:
        if self.zero:
            return "0–0"
        if self.over55:
            return "Over 5.5"
        if self.other:
            return "Over 3–3"
        return ""


@dataclass
class WatchRates:
    start: date
    end: date
    as_of: datetime
    sources: tuple[str, ...]
    teams: list[TeamSplit]
    clash: RateBucket
    rest: RateBucket
    clash_games: list[ClashGame]
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "as_of": self.as_of.isoformat(),
            "sources": list(self.sources),
            "errors": list(self.errors),
            "teams": [row.as_dict() for row in self.teams],
            "clash": self.clash.as_dict(),
            "rest": self.rest.as_dict(),
            "clash_games": [asdict(row) for row in self.clash_games],
        }


def default_fetch(url: str, timeout: int = 20) -> str | None:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": HTTP_UA})
    except requests.RequestException:
        return None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        return None
    text = response.content.decode("utf-8-sig", errors="replace")
    if "<html" in text[:240].lower():
        return None
    return text


def wiki_fetch(url: str, timeout: int = 25) -> str | None:
    """Wikipedia JSON parse API. Needs an identifying UA or it 403s."""
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": WIKI_UA})
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return response.content.decode("utf-8-sig", errors="replace")


class _HtmlTables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._skip or self._cell is None:
            return
        self._cell.append(data)


def parse_spl_score(cell: str) -> tuple[int, int] | None:
    match = _SCORE_CELL.search(cell or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_spl_results_html(html: str) -> list[tuple[str, str, int, int]]:
    """Home/away score grid from a Wikipedia Saudi Pro League page."""
    parser = _HtmlTables()
    parser.feed(html)
    games: list[tuple[str, str, int, int]] = []
    for table in parser.tables:
        if not _is_spl_results_grid(table):
            continue
        teams = [row[0] for row in table[1:] if row and row[0]]
        if len(teams) != len(table[0]) - 1:
            continue
        for i, home in enumerate(teams):
            cells = table[i + 1][1:]
            for j, away in enumerate(teams):
                if i == j or j >= len(cells):
                    continue
                score = parse_spl_score(cells[j])
                if score is None:
                    continue
                games.append((home, away, score[0], score[1]))
        if games:
            return games
    return games


def _is_spl_results_grid(table: list[list[str]]) -> bool:
    if len(table) < 8 or not table[0]:
        return False
    head = " ".join(table[0][:3]).lower()
    if "home" not in head or "away" not in head:
        return False
    return len(table) - 1 == len(table[0]) - 1


def _wiki_html(text: str) -> str:
    stripped = (text or "").lstrip()
    if not stripped.startswith("{"):
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    block = payload.get("parse") or {}
    inner = block.get("text")
    if isinstance(inner, dict):
        return str(inner.get("*") or "")
    if isinstance(inner, str):
        return inner
    return ""


def _put_game(
    games: dict[tuple, dict],
    day: date,
    home: str,
    away: str,
    hg: int,
    ag: int,
) -> bool:
    hq = watch_of(home)
    aq = watch_of(away)
    if not hq and not aq:
        return False
    key = (day.isoformat(), home.lower(), away.lower(), hg, ag)
    games[key] = {
        "date": day.isoformat(),
        "home": home,
        "away": away,
        "hg": hg,
        "ag": ag,
        "home_q": hq,
        "away_q": aq,
    }
    return True


def _ingest(
    text: str,
    cols: tuple[str, str, str, str, str],
    start: date,
    end: date,
    games: dict[tuple, dict],
) -> int:
    home_k, away_k, hg_k, ag_k, date_k = cols
    kept = 0
    for row in csv.DictReader(StringIO(text)):
        day = parse_day(str(row.get(date_k) or row.get("Date") or ""))
        if day is None or not (start <= day <= end):
            continue
        home = str(row.get(home_k) or "").strip()
        away = str(row.get(away_k) or "").strip()
        try:
            hg = int(float(row.get(hg_k)))
            ag = int(float(row.get(ag_k)))
        except (TypeError, ValueError):
            continue
        if _put_game(games, day, home, away, hg, ag):
            kept += 1
    return kept


def _ingest_spl_wiki(
    text: str,
    day: date,
    games: dict[tuple, dict],
) -> int:
    html = _wiki_html(text)
    kept = 0
    for home, away, hg, ag in parse_spl_results_html(html):
        if _put_game(games, day, home, away, hg, ag):
            kept += 1
    return kept


def load_watch_rates(
    *,
    days: int = 365,
    now: datetime | None = None,
    fetch_fn: FetchFn | None = None,
) -> WatchRates:
    """Rolling window of watchlist 0–0 / Over 5.5 / Over 3–3, home-away-clash."""
    clock = now or datetime.now(timezone.utc)
    end = clock.date()
    start = end - timedelta(days=max(30, days))
    getter = fetch_fn or default_fetch
    urls: list[tuple[str, str, tuple[str, str, str, str, str]]] = []
    for folder in season_folders(start, end):
        for code, name in LEAGUE_FILES:
            urls.append(
                (
                    f"{RESULTS_CSV_HOST}/{folder}/{code}.csv",
                    f"{name} {folder}",
                    ("HomeTeam", "AwayTeam", "FTHG", "FTAG", "Date"),
                )
            )
    for url, name, cols in EXTRA_FILES:
        urls.append((url, name, cols))
    urls.append(
        (INTL_URL, "Internationals", ("home_team", "away_team", "home_score", "away_score", "date"))
    )

    games: dict[tuple, dict] = {}
    sources: list[str] = []
    errors: list[str] = []

    def one(item: tuple[str, str, tuple[str, str, str, str, str]]) -> tuple[str, str | None]:
        url, name, _cols = item
        return name, getter(url)

    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(one, urls))
    by_name = {name: body for name, body in fetched}
    for url, name, cols in urls:
        text = by_name.get(name)
        if not text:
            errors.append(name)
            continue
        n = _ingest(text, cols, start, end, games)
        sources.append(f"{name}: {n}")

    wiki_getter = fetch_fn or wiki_fetch
    for year in overlapping_spl_seasons(start, end):
        day = spl_overlap_day(year, start, end)
        name = f"Saudi Pro League {year}-{str(year + 1)[2:]}"
        if day is None:
            continue
        text = wiki_getter(spl_wiki_url(year))
        if not text:
            errors.append(name)
            continue
        n = _ingest_spl_wiki(text, day, games)
        sources.append(f"{name}: {n}")

    teams = {
        query: TeamSplit(
            query=query,
            label=watch_display_name(query),
            kind="nation" if query in WATCH_NATIONS else "club",
        )
        for query in list(WATCH_CLUBS) + list(WATCH_NATIONS)
    }
    clash = RateBucket()
    rest = RateBucket()
    clash_games: list[ClashGame] = []
    for rec in games.values():
        hg, ag = rec["hg"], rec["ag"]
        is_clash = bool(rec["home_q"] and rec["away_q"])
        if is_clash:
            clash.add(hg, ag)
            home_q, away_q = rec["home_q"], rec["away_q"]
            kind = "nation" if home_q in WATCH_NATIONS or away_q in WATCH_NATIONS else "club"
            zero, over55, other = score_flags(hg, ag)
            clash_games.append(
                ClashGame(
                    date=rec["date"],
                    home=watch_display_name(home_q),
                    away=watch_display_name(away_q),
                    score=f"{hg}-{ag}",
                    zero=zero,
                    over55=over55,
                    other=other,
                    kind=kind,
                )
            )
        else:
            rest.add(hg, ag)
        home_team = teams.get(rec["home_q"])
        away_team = teams.get(rec["away_q"])
        if home_team:
            home_team.home.add(hg, ag)
            (home_team.clash if is_clash else home_team.rest).add(hg, ag)
        if away_team:
            away_team.away.add(hg, ag)
            (away_team.clash if is_clash else away_team.rest).add(hg, ag)

    clash_games.sort(key=lambda row: (row.date, row.home, row.away))
    ranked = sorted(
        teams.values(),
        key=lambda row: (
            -(row.home.over55_rate + row.away.over55_rate) / 2,
            row.label,
        ),
    )
    return WatchRates(
        start=start,
        end=end,
        as_of=clock,
        sources=tuple(sources),
        teams=ranked,
        clash=clash,
        rest=rest,
        clash_games=clash_games,
        errors=tuple(errors),
    )
