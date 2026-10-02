from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Callable

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

FetchFn = Callable[[str], str | None]


def season_folders(start: date, end: date) -> tuple[str, ...]:
    """football-data.co.uk mmz4281 keys that can overlap [start, end]."""
    years: set[int] = set()
    for day in (start, end):
        years.add(day.year if day.month >= 7 else day.year - 1)
        years.add((day.year if day.month >= 7 else day.year - 1) - 1)
    return tuple(f"{year % 100:02d}{(year + 1) % 100:02d}" for year in sorted(years))


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
        hq = watch_of(home)
        aq = watch_of(away)
        if not hq and not aq:
            continue
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
