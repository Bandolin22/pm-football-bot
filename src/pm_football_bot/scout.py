from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import requests

from pm_football_bot.config import hydrate_env

FOOTBALL_DATA_HOST = "https://api.football-data.org/v4"
COMPETITION_CODE = {
    "epl": "PL",
    "laliga": "PD",
    "bundesliga": "BL1",
    "seriea": "SA",
    "ligue1": "FL1",
    "ucl": "CL",
}

# football-data.org free: results, table, H2H, rest. Not Opta xG / predicted XI.
MISSING_ON_FREE_TIER = (
    "xG / xGA (needs FBref, Understat, or a paid Stats Perform feed)",
    "Predicted XI and injury list (Opta / Stats Perform is licensed, not a public API)",
    "PPDA, set-piece xG, and style labels",
    "Sharp vs public money (Polymarket mid is the only market we already have)",
)

_STRIP = re.compile(
    r"\b(fc|cf|afc|sc|ac|as|ss|us|rc|rcd|vfb|vfl|tsv|sv|tsg|calcio|"
    r"club|de|the|united states)\b",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9]+")
_ALIASES = {
    "inter milan": "internazionale milano",
    "internazionale": "internazionale milano",
    "bayern munich": "bayern munchen",
    "man utd": "manchester united",
    "man united": "manchester united",
    "psg": "paris saint germain",
    "athletic bilbao": "athletic",
    "wolves": "wolverhampton wanderers",
    "spurs": "tottenham hotspur",
}
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 45 * 60


class ScoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class TeamPulse:
    name: str
    position: int | None
    played: int | None
    points: int | None
    goal_diff: int | None
    form: str
    last_five: tuple[str, ...]
    home_ppg: float | None
    away_ppg: float | None
    gf_pg: float | None
    ga_pg: float | None
    rest_days: int | None
    next_match: str | None


@dataclass(frozen=True)
class Briefing:
    home_name: str
    away_name: str
    home: TeamPulse | None
    away: TeamPulse | None
    h2h: tuple[str, ...]
    vetoes: tuple[str, ...]
    missing: tuple[str, ...] = MISSING_ON_FREE_TIER
    error: str | None = None
    sources: tuple[str, ...] = ("football-data.org",)


def football_data_token() -> str | None:
    hydrate_env()
    token = (os.environ.get("FOOTBALL_DATA_TOKEN") or os.environ.get("FOOTBALL_DATA_API_KEY") or "").strip()
    return token or None


def split_fixture(title: str) -> tuple[str, str] | None:
    text = (title or "").strip()
    for sep in (" vs. ", " vs ", " v "):
        if sep in text:
            home, away = text.split(sep, 1)
            home, away = home.strip(), away.strip()
            if home and away:
                return home, away
    return None


def fold_name(name: str) -> str:
    text = (name or "").lower().replace("ü", "u").replace("ö", "o").replace("ä", "a")
    text = text.replace("é", "e").replace("è", "e").replace("ñ", "n").replace("&", " and ")
    text = _STRIP.sub(" ", text)
    text = _PUNCT.sub(" ", text).strip()
    return _ALIASES.get(text, text)


def match_team(query: str, teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = fold_name(query)
    if not target or not teams:
        return None
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in teams:
        best = 0.0
        for raw in (row.get("name"), row.get("shortName")):
            best = max(best, _name_score(target, fold_name(str(raw or ""))))
        tla = fold_name(str(row.get("tla") or ""))
        if tla and tla == target:
            best = max(best, 0.99)
        ranked.append((best, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if ranked and ranked[0][0] >= 0.72:
        return ranked[0][1]
    return None


def _name_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    q_tokens = set(query.split())
    c_tokens = set(candidate.split())
    if q_tokens and q_tokens <= c_tokens:
        return 0.96
    if c_tokens and c_tokens <= q_tokens and min(len(t) for t in c_tokens) >= 4:
        return 0.9
    if len(query) >= 6 and len(candidate) >= 6 and (query in candidate or candidate in query):
        return 0.92
    return SequenceMatcher(None, query, candidate).ratio()


def load_briefing(
    league: str,
    fixture: str,
    kickoff: datetime | None,
    favorite_team: str,
    session: requests.Session | None = None,
) -> Briefing:
    sides = split_fixture(fixture)
    if sides is None:
        return Briefing("", "", None, None, (), (), error="Could not parse home / away from the fixture title.")
    home_name, away_name = sides
    code = COMPETITION_CODE.get(league)
    if code is None:
        return Briefing(home_name, away_name, None, None, (), (), error=f"No football-data.org competition for {league}.")
    if football_data_token() is None:
        return Briefing(
            home_name,
            away_name,
            None,
            None,
            (),
            (),
            error="Set FOOTBALL_DATA_TOKEN in .env (free key at https://www.football-data.org/client/register).",
        )

    client = session or requests.Session()
    try:
        teams = _competition_teams(client, code)
        standings = _competition_standings(client, code)
        home_row = match_team(home_name, teams)
        away_row = match_team(away_name, teams)
        if home_row is None or away_row is None:
            return Briefing(
                home_name,
                away_name,
                None,
                None,
                (),
                (),
                error=f"Could not map teams on football-data.org ({home_name} / {away_name}).",
            )
        home_id = int(home_row["id"])
        away_id = int(away_row["id"])
        home_matches = _team_matches(client, home_id, "FINISHED", 12)
        away_matches = _team_matches(client, away_id, "FINISHED", 12)
        home_next = _team_matches(client, home_id, "SCHEDULED", 3)
        away_next = _team_matches(client, away_id, "SCHEDULED", 3)
    except ScoutError as exc:
        return Briefing(home_name, away_name, None, None, (), (), error=str(exc))
    except requests.RequestException as exc:
        return Briefing(home_name, away_name, None, None, (), (), error=f"football-data.org request failed: {exc}")

    home = _pulse(home_row, home_id, standings, home_matches, home_next, kickoff)
    away = _pulse(away_row, away_id, standings, away_matches, away_next, kickoff)
    h2h = _h2h(home_id, away_id, home_matches + away_matches)
    vetoes = veto_notes(home, away, favorite_team, home_name, away_name)
    return Briefing(home_name, away_name, home, away, h2h, vetoes)


def veto_notes(
    home: TeamPulse | None,
    away: TeamPulse | None,
    favorite_team: str,
    home_name: str,
    away_name: str,
) -> tuple[str, ...]:
    notes: list[str] = []
    if home is None or away is None:
        return ()
    fav_is_home = fold_name(favorite_team) == fold_name(home_name) or fold_name(favorite_team) in fold_name(home_name)
    fav_is_away = fold_name(favorite_team) == fold_name(away_name) or fold_name(favorite_team) in fold_name(away_name)
    if not fav_is_home and not fav_is_away:
        fav_is_away = SequenceMatcher(None, fold_name(favorite_team), fold_name(away_name)).ratio() > SequenceMatcher(
            None, fold_name(favorite_team), fold_name(home_name)
        ).ratio()
        fav_is_home = not fav_is_away
    fav = home if fav_is_home else away
    dog = away if fav_is_home else home
    if fav_is_away and fav.away_ppg is not None and fav.away_ppg < 1.0:
        notes.append(
            f"Skip fade_dog: favorite is away at {fav.away_ppg:.2f} away PPG (Hull–United style trap)."
        )
    if fav_is_away and dog.home_ppg is not None and dog.home_ppg >= 1.6 and (fav.away_ppg is None or fav.away_ppg < 1.2):
        notes.append("Skip fade_dog: dog is a solid home side against a weak-away favorite.")
    losses = fav.form.replace(",", "").upper().count("L")
    if losses >= 3:
        notes.append(f"Caution: favorite form is {fav.form or '—'} — not a fortress.")
    if fav.rest_days is not None and dog.rest_days is not None and fav.rest_days <= 2 and dog.rest_days >= 6:
        notes.append("Caution: favorite is on a short rest vs a fresher dog.")
    return tuple(notes)


def _cached(key: str, loader):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = loader()
    _CACHE[key] = (now, value)
    return value


def _get(client: requests.Session, path: str, params: dict[str, Any] | None = None) -> Any:
    token = football_data_token()
    if not token:
        raise ScoutError("Set FOOTBALL_DATA_TOKEN in .env.")
    response = client.get(
        f"{FOOTBALL_DATA_HOST}{path}",
        params=params,
        headers={"X-Auth-Token": token},
        timeout=30,
    )
    if response.status_code == 429:
        raise ScoutError("football-data.org rate limit (free tier is 10 calls/min). Wait a minute and retry.")
    if response.status_code in {401, 403}:
        raise ScoutError("football-data.org rejected the token. Check FOOTBALL_DATA_TOKEN.")
    response.raise_for_status()
    return response.json()


def _competition_teams(client: requests.Session, code: str) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        data = _get(client, f"/competitions/{code}/teams")
        return list(data.get("teams") or [])

    return _cached(f"teams:{code}", load)


def _competition_standings(client: requests.Session, code: str) -> dict[str, list[dict[str, Any]]]:
    def load() -> dict[str, list[dict[str, Any]]]:
        data = _get(client, f"/competitions/{code}/standings")
        out: dict[str, list[dict[str, Any]]] = {}
        for block in data.get("standings") or []:
            kind = str(block.get("type") or "TOTAL").upper()
            out[kind] = list(block.get("table") or [])
        return out

    return _cached(f"table:{code}", load)


def _team_matches(client: requests.Session, team_id: int, status: str, limit: int) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        data = _get(
            client,
            f"/teams/{team_id}/matches",
            {"status": status, "limit": limit, "competitions": ",".join(COMPETITION_CODE.values())},
        )
        return list(data.get("matches") or [])

    return _cached(f"matches:{team_id}:{status}:{limit}", load)


def _table_row(table: list[dict[str, Any]], team_id: int) -> dict[str, Any] | None:
    for row in table:
        team = row.get("team") or {}
        if int(team.get("id") or 0) == team_id:
            return row
    return None


def _ppg(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    played = int(row.get("playedGames") or 0)
    points = int(row.get("points") or 0)
    if played <= 0:
        return None
    return round(points / played, 2)


def _pg(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    played = int(row.get("playedGames") or 0)
    if played <= 0:
        return None
    return round(int(row.get(key) or 0) / played, 2)


def _parse_when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _result_for(team_id: int, match: dict[str, Any]) -> str | None:
    score = (match.get("score") or {}).get("fullTime") or {}
    home = score.get("home")
    away = score.get("away")
    if home is None or away is None:
        return None
    home_id = int((match.get("homeTeam") or {}).get("id") or 0)
    if home == away:
        return "D"
    won = (team_id == home_id and home > away) or (team_id != home_id and away > home)
    return "W" if won else "L"


def _form(team_id: int, matches: list[dict[str, Any]], n: int = 5) -> str:
    finished = [m for m in matches if str(m.get("status") or "") == "FINISHED"]
    finished.sort(key=lambda m: str(m.get("utcDate") or ""), reverse=True)
    letters = []
    for match in finished[:n]:
        letter = _result_for(team_id, match)
        if letter:
            letters.append(letter)
    return "".join(letters)


def _last_five(team_id: int, matches: list[dict[str, Any]]) -> tuple[str, ...]:
    finished = [m for m in matches if str(m.get("status") or "") == "FINISHED"]
    finished.sort(key=lambda m: str(m.get("utcDate") or ""), reverse=True)
    lines = []
    for match in finished[:5]:
        letter = _result_for(team_id, match) or "?"
        home = (match.get("homeTeam") or {}).get("shortName") or (match.get("homeTeam") or {}).get("name")
        away = (match.get("awayTeam") or {}).get("shortName") or (match.get("awayTeam") or {}).get("name")
        score = (match.get("score") or {}).get("fullTime") or {}
        lines.append(f"{letter} {home} {score.get('home')}–{score.get('away')} {away}")
    return tuple(lines)


def _rest_days(team_id: int, matches: list[dict[str, Any]], kickoff: datetime | None) -> int | None:
    if kickoff is None:
        return None
    finished = [m for m in matches if str(m.get("status") or "") == "FINISHED"]
    last = None
    for match in finished:
        stamp = _parse_when(match.get("utcDate"))
        if stamp is None:
            continue
        if last is None or stamp > last:
            last = stamp
    if last is None:
        return None
    return max(0, int((kickoff - last).total_seconds() // 86400))


def _next_line(team_id: int, matches: list[dict[str, Any]], skip_fixture_kickoff: datetime | None) -> str | None:
    upcoming = [m for m in matches if str(m.get("status") or "") in {"SCHEDULED", "TIMED"}]
    upcoming.sort(key=lambda m: str(m.get("utcDate") or ""))
    for match in upcoming:
        stamp = _parse_when(match.get("utcDate"))
        if skip_fixture_kickoff and stamp and abs((stamp - skip_fixture_kickoff).total_seconds()) < 12 * 3600:
            continue
        home = (match.get("homeTeam") or {}).get("shortName") or (match.get("homeTeam") or {}).get("name")
        away = (match.get("awayTeam") or {}).get("shortName") or (match.get("awayTeam") or {}).get("name")
        when = stamp.strftime("%d %b %H:%M UTC") if stamp else "?"
        return f"{home} vs {away} · {when}"
    return None


def _pulse(
    team: dict[str, Any],
    team_id: int,
    standings: dict[str, list[dict[str, Any]]],
    finished: list[dict[str, Any]],
    upcoming: list[dict[str, Any]],
    kickoff: datetime | None,
) -> TeamPulse:
    total = _table_row(standings.get("TOTAL") or [], team_id)
    home = _table_row(standings.get("HOME") or [], team_id)
    away = _table_row(standings.get("AWAY") or [], team_id)
    form = ""
    if total and total.get("form"):
        form = str(total.get("form") or "").replace(",", "").replace(" ", "")
    if not form:
        form = _form(team_id, finished)
    return TeamPulse(
        name=str(team.get("name") or ""),
        position=int(total["position"]) if total and total.get("position") is not None else None,
        played=int(total["playedGames"]) if total and total.get("playedGames") is not None else None,
        points=int(total["points"]) if total and total.get("points") is not None else None,
        goal_diff=int(total["goalDifference"]) if total and total.get("goalDifference") is not None else None,
        form=form,
        last_five=_last_five(team_id, finished),
        home_ppg=_ppg(home),
        away_ppg=_ppg(away),
        gf_pg=_pg(total, "goalsFor"),
        ga_pg=_pg(total, "goalsAgainst"),
        rest_days=_rest_days(team_id, finished, kickoff),
        next_match=_next_line(team_id, upcoming, kickoff),
    )


def _h2h(home_id: int, away_id: int, matches: list[dict[str, Any]]) -> tuple[str, ...]:
    ids = {home_id, away_id}
    seen: set[str] = set()
    lines: list[tuple[str, str]] = []
    for match in matches:
        hid = int((match.get("homeTeam") or {}).get("id") or 0)
        aid = int((match.get("awayTeam") or {}).get("id") or 0)
        if {hid, aid} != ids:
            continue
        key = str(match.get("id") or match.get("utcDate") or "")
        if key in seen:
            continue
        seen.add(key)
        if str(match.get("status") or "") != "FINISHED":
            continue
        score = (match.get("score") or {}).get("fullTime") or {}
        home = (match.get("homeTeam") or {}).get("shortName") or (match.get("homeTeam") or {}).get("name")
        away = (match.get("awayTeam") or {}).get("shortName") or (match.get("awayTeam") or {}).get("name")
        stamp = _parse_when(match.get("utcDate"))
        when = stamp.strftime("%b %Y") if stamp else ""
        lines.append((str(match.get("utcDate") or ""), f"{when} {home} {score.get('home')}–{score.get('away')} {away}"))
    lines.sort(key=lambda item: item[0], reverse=True)
    return tuple(row for _, row in lines[:6])
