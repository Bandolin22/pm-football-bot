from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from pm_football_bot.config import League
from pm_football_bot.gamma import GammaClient
from pm_football_bot.models import BinaryMarket, Fixture, SideQuote, utcnow
from pm_football_bot.scout import _name_score, fold_name, split_fixture

POLYMARKET_EVENT = "https://polymarket.com/event/{slug}"
DEFAULT_LIMIT = 20
FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)
LEAGUE_ORDER = (
    "epl",
    "laliga",
    "ligue1",
    "seriea",
    "bundesliga",
    "por",
    "ucl",
    "uel",
    "col",
    "efl",
    "elc",
    "efa",
    "cdr",
    "dfb",
    "itc",
    "cde",
    "ssc",
    "isc",
    "gsc",
    "frtc",
    "usc",
    "cwc",
    "ecs",
)

# User watchlist, including the nicknames / spellings they typed.
WATCH_QUERIES = (
    "Real Madrid",
    "Barca",
    "Atletic Madrid",
    "Arsenal",
    "Liverpool",
    "Man city",
    "man united",
    "chelsea",
    "Spurs",
    "Inter milan",
    "AC milan",
    "juventus",
    "Atlanta vergamou",
    "bayerun munchen",
    "vorusia dortmound",
    "PSG",
    "SSC Napoli",
    "Como 1907",
    "Lazio",
    "AS Roma",
    "Sporting CP",
    "FC Porto",
    "SL Benfica",
)

_WATCH_ALIASES = {
    "barca": "barcelona",
    "atletic madrid": "atletico madrid",
    "athletic madrid": "atletico madrid",
    "atletico": "atletico madrid",
    "man city": "manchester city",
    "mancity": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "inter milan": "internazionale milano",
    "inter": "internazionale milano",
    "ac milan": "milan",
    "juve": "juventus",
    "atlanta": "atalanta",
    "atlanta vergamou": "atalanta",
    "atalanta vergamou": "atalanta",
    "atalanta bergamo": "atalanta",
    "bayerun munchen": "bayern munchen",
    "bayern": "bayern munchen",
    "bayern munich": "bayern munchen",
    "vorusia dortmound": "borussia dortmund",
    "dortmund": "borussia dortmund",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "ssc napoli": "napoli",
    "como 1907": "como",
    "ss lazio": "lazio",
    "as roma": "roma",
    "sporting cp": "sporting",
    "sporting lisbon": "sporting",
    "sporting lisboa": "sporting",
    "sporting gij n": "sporting gijon",
    "fc porto": "porto",
    "sl benfica": "benfica",
    "sport lisboa e benfica": "benfica",
    "sport lisboa benfica": "benfica",
}

# Names that contain a watch token but are a different club.
_WATCH_NEGATIVES = {
    "barcelona": ("espanyol",),
    "sporting": ("kansas", "kc", "gijon", "gij", "braga"),
    "porto": ("alegre",),
}

WATCH_LABELS = {
    "Real Madrid": "Real Madrid",
    "Barca": "Barcelona",
    "Atletic Madrid": "Atlético Madrid",
    "Arsenal": "Arsenal",
    "Liverpool": "Liverpool",
    "Man city": "Manchester City",
    "man united": "Manchester United",
    "chelsea": "Chelsea",
    "Spurs": "Tottenham",
    "Inter milan": "Inter",
    "AC milan": "AC Milan",
    "juventus": "Juventus",
    "Atlanta vergamou": "Atalanta",
    "bayerun munchen": "Bayern",
    "vorusia dortmound": "Dortmund",
    "PSG": "PSG",
    "SSC Napoli": "Napoli",
    "Como 1907": "Como",
    "Lazio": "Lazio",
    "AS Roma": "Roma",
    "Sporting CP": "Sporting",
    "FC Porto": "Porto",
    "SL Benfica": "Benfica",
}


def watch_display_name(query: str) -> str:
    return WATCH_LABELS.get(query, query)


@dataclass(frozen=True)
class UpcomingMatch:
    league: str
    league_name: str
    title: str
    slug: str
    kickoff: datetime | None
    home_team: str
    away_team: str
    home_pct: float | None
    draw_pct: float | None
    away_pct: float | None
    watch: bool = False

    @property
    def url(self) -> str:
        return poly_event_url(self.slug)


def poly_event_url(slug: str) -> str:
    return POLYMARKET_EVENT.format(slug=slug.strip())


def yes_mid(quote: SideQuote | None) -> float | None:
    if quote is None:
        return None
    return quote.yes.mid


def draw_mid(market: BinaryMarket | None) -> float | None:
    if market is None:
        return None
    yes = market.outcome("Yes")
    return None if yes is None else yes.mid


def is_upcoming(fixture: Fixture, now: datetime) -> bool:
    if fixture.kickoff is None:
        return True
    return fixture.kickoff >= now


def take_upcoming(
    fixtures: list[Fixture],
    now: datetime,
    limit: int | None = DEFAULT_LIMIT,
) -> list[Fixture]:
    future = [row for row in fixtures if is_upcoming(row, now)]
    future.sort(key=lambda row: (row.kickoff or FAR_FUTURE, row.title))
    if limit is None:
        return future
    return future[: max(0, limit)]


def from_fixture(fixture: Fixture, league_name: str) -> UpcomingMatch:
    home = fixture.home.team if fixture.home else ""
    away = fixture.away.team if fixture.away else ""
    if (not home or not away) and fixture.title:
        sides = split_fixture(fixture.title)
        if sides:
            home = home or sides[0]
            away = away or sides[1]
    return UpcomingMatch(
        league=fixture.league,
        league_name=league_name,
        title=fixture.title,
        slug=fixture.slug,
        kickoff=fixture.kickoff,
        home_team=home,
        away_team=away,
        home_pct=yes_mid(fixture.home),
        draw_pct=draw_mid(fixture.draw),
        away_pct=yes_mid(fixture.away),
        watch=involves_watch_club(title=fixture.title, home_team=home, away_team=away),
    )


def list_upcoming(
    client: GammaClient,
    leagues: tuple[League, ...],
    *,
    now: datetime | None = None,
    per_league: int | None = DEFAULT_LIMIT,
    league_keys: set[str] | None = None,
    include_disabled: bool = True,
) -> list[UpcomingMatch]:
    """Next moneyline fixtures per league, including UCL even when harvest is off."""
    now = now or utcnow()
    wanted = [
        league
        for league in _ordered(leagues)
        if (league_keys is None or league.key in league_keys)
        and (include_disabled or league.enabled)
    ]

    def _load(league: League) -> list[UpcomingMatch]:
        events = client.list_moneyline_events(league)
        fixtures = [client.parse_moneyline(league, event) for event in events]
        picked = take_upcoming(fixtures, now, per_league)
        return [from_fixture(item, league.name) for item in picked]

    rows: list[UpcomingMatch] = []
    workers = min(8, max(1, len(wanted)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for chunk in pool.map(_load, wanted):
            rows.extend(chunk)
    rows.sort(key=lambda row: (row.kickoff or FAR_FUTURE, _league_rank(row.league), row.title))
    return rows


def soonest(matches: list[UpcomingMatch], limit: int = DEFAULT_LIMIT) -> list[UpcomingMatch]:
    return matches[: max(0, limit)]


def watch_first(rows: list[dict]) -> list[dict]:
    watched = [row for row in rows if row.get("watch")]
    rest = [row for row in rows if not row.get("watch")]
    return watched + rest


def involves_watch_club(*, title: str = "", home_team: str = "", away_team: str = "") -> bool:
    candidates = [home_team, away_team]
    sides = split_fixture(title)
    if sides:
        candidates.extend(sides)
    if title:
        candidates.append(title)
    return any(_is_watch_name(item) for item in candidates if item)


def matched_watch_club(name: str) -> str | None:
    """Return the watchlist label that matches this club, if any."""
    candidate = _watch_fold(name)
    folded = fold_name(name)
    if not candidate:
        return None
    hits: list[tuple[int, str]] = []
    for query in WATCH_QUERIES:
        watch = _watch_fold(query)
        blocked = _WATCH_NEGATIVES.get(watch, ())
        haystack = f"{folded} {candidate}"
        if any(bit in haystack.split() for bit in blocked):
            continue
        if _watch_hit(watch, candidate):
            hits.append((len(watch), query))
    if not hits:
        return None
    return max(hits, key=lambda row: row[0])[1]


def _watch_fold(name: str) -> str:
    folded = fold_name(name)
    return _WATCH_ALIASES.get(folded, folded)


def _is_watch_name(name: str) -> bool:
    candidate = _watch_fold(name)
    folded = fold_name(name)
    if not candidate:
        return False
    for query in WATCH_QUERIES:
        watch = _watch_fold(query)
        blocked = _WATCH_NEGATIVES.get(watch, ())
        haystack = f"{folded} {candidate}"
        if any(bit in haystack.split() for bit in blocked):
            continue
        if _watch_hit(watch, candidate):
            return True
    return False


def _watch_hit(watch: str, candidate: str) -> bool:
    if watch == candidate:
        return True
    w_tokens = set(watch.split())
    c_tokens = set(candidate.split())
    if w_tokens and w_tokens <= c_tokens:
        return True
    if len(watch) >= 6 and watch in candidate:
        return True
    # Fuzzy for typos, but not "Paris FC" vs "Paris Saint-Germain".
    if abs(len(watch) - len(candidate)) > 6:
        return False
    return _name_score(watch, candidate) >= 0.84


def as_record(match: UpcomingMatch) -> dict:
    return {
        "league": match.league,
        "league_name": match.league_name,
        "title": match.title,
        "slug": match.slug,
        "kickoff": match.kickoff.isoformat() if match.kickoff else None,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_pct": match.home_pct,
        "draw_pct": match.draw_pct,
        "away_pct": match.away_pct,
        "url": match.url,
        "watch": match.watch,
    }


def _ordered(leagues: tuple[League, ...]) -> tuple[League, ...]:
    rank = {key: index for index, key in enumerate(LEAGUE_ORDER)}
    return tuple(sorted(leagues, key=lambda row: (rank.get(row.key, 99), row.key)))


def _league_rank(key: str) -> int:
    try:
        return LEAGUE_ORDER.index(key)
    except ValueError:
        return 99
