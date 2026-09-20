from __future__ import annotations

import csv
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import StringIO
from typing import Any

import requests

from pm_football_bot.config import hydrate_env

FOOTBALL_DATA_HOST = "https://api.football-data.org/v4"
RESULTS_CSV_HOST = "https://www.football-data.co.uk/mmz4281"
HTTP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
COMPETITION_CODE = {
    "epl": "PL",
    "laliga": "PD",
    "bundesliga": "BL1",
    "seriea": "SA",
    "ligue1": "FL1",
    "ucl": "CL",
    "uel": "EL",
    "por": "PPL",
    "ned": "DED",
    "bel": "BJL",
    "sco": "SPL",
    "bra": "BSA",
    "elc": "ELC",
    "efa": "FAC",
    "dfb": "DFB",
    "cdr": "CDR",
}
CSV_CODE = {
    "epl": "E0",
    "elc": "E1",
    "laliga": "SP1",
    "bundesliga": "D1",
    "seriea": "I1",
    "ligue1": "F1",
    "ned": "N1",
    "bel": "B1",
    "por": "P1",
    "sco": "SC0",
    "tur": "T1",
}

# football-data.co.uk results CSV is primary (no key, no 10/min cap).
# football-data.org API is fallback for cups and mapping misses.
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
    "barca": "barcelona",
    "man city": "manchester city",
    "ath madrid": "atletico madrid",
    "ath bilbao": "athletic",
    "paris sg": "paris saint germain",
    "inter": "internazionale milano",
    "sporting cp": "sporting",
    "sp lisbon": "sporting",
    "sp braga": "braga",
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
    last_five_scores: tuple[tuple[int, int], ...] = ()


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
    sources: tuple[str, ...] = ("football-data.co.uk",)


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
    text = text.replace("ø", "o").replace("ç", "c").replace("ş", "s").replace("ğ", "g").replace("ı", "i")
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
        for raw in (row.get("name"), row.get("shortName"), row.get("location")):
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
    client = session or requests.Session()
    errors: list[str] = []
    csv_code = CSV_CODE.get(league)
    if csv_code:
        try:
            return _briefing_from_results_csv(client, league, home_name, away_name, kickoff, favorite_team)
        except ScoutError as exc:
            errors.append(str(exc))
        except requests.RequestException as exc:
            errors.append(f"football-data.co.uk request failed: {exc}")
    code = COMPETITION_CODE.get(league)
    if code and football_data_token():
        try:
            return _briefing_from_football_data(client, code, home_name, away_name, kickoff, favorite_team)
        except ScoutError as exc:
            errors.append(str(exc))
        except requests.RequestException as exc:
            errors.append(f"football-data.org request failed: {exc}")
    if not csv_code and code is None:
        return Briefing(home_name, away_name, None, None, (), (), error=f"No results feed for {league}.")
    if not csv_code and football_data_token() is None:
        return Briefing(
            home_name,
            away_name,
            None,
            None,
            (),
            (),
            error="Set FOOTBALL_DATA_TOKEN in .env (free key at https://www.football-data.org/client/register).",
        )
    return Briefing(
        home_name,
        away_name,
        None,
        None,
        (),
        (),
        error="; ".join(errors) or "No form source available.",
        sources=(),
    )


def _briefing_from_results_csv(
    client: requests.Session,
    league: str,
    home_name: str,
    away_name: str,
    kickoff: datetime | None,
    favorite_team: str,
) -> Briefing:
    teams, matches, standings = _csv_league_pack(client, league)
    home_row = match_team(home_name, teams)
    away_row = match_team(away_name, teams)
    if home_row is None or away_row is None:
        raise ScoutError(f"Could not map teams on football-data.co.uk ({home_name} / {away_name}).")
    home_id = int(home_row["id"])
    away_id = int(away_row["id"])
    home_matches = _matches_for_team(matches, home_id)
    away_matches = _matches_for_team(matches, away_id)
    home_finished = [row for row in home_matches if row["status"] == "FINISHED"]
    away_finished = [row for row in away_matches if row["status"] == "FINISHED"]
    home = _pulse(home_row, home_id, standings, home_finished, [], kickoff)
    away = _pulse(away_row, away_id, standings, away_finished, [], kickoff)
    h2h = _h2h(home_id, away_id, home_finished + away_finished)
    vetoes = veto_notes(home, away, favorite_team, home_name, away_name)
    return Briefing(home_name, away_name, home, away, h2h, vetoes, sources=("football-data.co.uk",))


def _briefing_from_football_data(
    client: requests.Session,
    code: str,
    home_name: str,
    away_name: str,
    kickoff: datetime | None,
    favorite_team: str,
) -> Briefing:
    teams = _competition_teams(client, code)
    standings = _competition_standings(client, code)
    home_row = match_team(home_name, teams)
    away_row = match_team(away_name, teams)
    if home_row is None or away_row is None:
        raise ScoutError(f"Could not map teams on football-data.org ({home_name} / {away_name}).")
    home_id = int(home_row["id"])
    away_id = int(away_row["id"])
    finished = _competition_matches(client, code, "FINISHED")
    scheduled = _competition_matches(client, code, "SCHEDULED")
    home_matches = _matches_for_team(finished, home_id)
    away_matches = _matches_for_team(finished, away_id)
    home_next = _matches_for_team(scheduled, home_id)
    away_next = _matches_for_team(scheduled, away_id)
    home = _pulse(home_row, home_id, standings, home_matches, home_next, kickoff)
    away = _pulse(away_row, away_id, standings, away_matches, away_next, kickoff)
    h2h = _h2h(home_id, away_id, home_matches + away_matches)
    vetoes = veto_notes(home, away, favorite_team, home_name, away_name)
    return Briefing(home_name, away_name, home, away, h2h, vetoes, sources=("football-data.org",))


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


def _csv_season(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _csv_when(date: str, clock: str) -> str:
    text = f"{(date or '').strip()} {(clock or '00:00').strip()}"
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"):
        try:
            stamp = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return stamp.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return ""


def _csv_rows(client: requests.Session, league: str) -> list[dict[str, str]]:
    code = CSV_CODE.get(league)
    if not code:
        raise ScoutError(f"No football-data.co.uk CSV for {league}.")
    season = _csv_season()
    url = f"{RESULTS_CSV_HOST}/{season}/{code}.csv"

    def load() -> list[dict[str, str]]:
        response = client.get(url, timeout=30, headers={"User-Agent": HTTP_UA})
        if response.status_code == 404:
            raise ScoutError(f"football-data.co.uk has no {season}/{code}.csv yet.")
        response.raise_for_status()
        body = response.content.decode("utf-8-sig", errors="replace")
        if "Div" not in body[:120] or "HomeTeam" not in body[:200]:
            raise ScoutError(f"football-data.co.uk returned a non-CSV page for {league}.")
        return list(csv.DictReader(StringIO(body)))

    return _cached(f"csv:{season}:{code}", load)


def _csv_league_pack(
    client: requests.Session, league: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    def load() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        rows = _csv_rows(client, league)
        ids: dict[str, int] = {}
        labels: dict[int, str] = {}

        def team_id(name: str) -> int:
            key = fold_name(name)
            if key not in ids:
                ids[key] = len(ids) + 1
                labels[ids[key]] = name
            return ids[key]

        matches: list[dict[str, Any]] = []
        for row in rows:
            home = str(row.get("HomeTeam") or "").strip()
            away = str(row.get("AwayTeam") or "").strip()
            if not home or not away:
                continue
            try:
                home_goals = int(row.get("FTHG"))
                away_goals = int(row.get("FTAG"))
            except (TypeError, ValueError):
                continue
            hid = team_id(home)
            aid = team_id(away)
            matches.append(
                {
                    "id": f"{row.get('Date')}:{home}:{away}",
                    "status": "FINISHED",
                    "utcDate": _csv_when(str(row.get("Date") or ""), str(row.get("Time") or "")),
                    "homeTeam": {"id": hid, "name": home, "shortName": home},
                    "awayTeam": {"id": aid, "name": away, "shortName": away},
                    "score": {"fullTime": {"home": home_goals, "away": away_goals}},
                }
            )
        teams = [
            {"id": tid, "name": labels[tid], "shortName": labels[tid], "tla": "", "location": labels[tid]}
            for tid in sorted(labels)
        ]
        return teams, matches, _standings_from_matches(matches)

    return _cached(f"csv-pack:{league}:{_csv_season()}", load)


def _standings_from_matches(matches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    def blank() -> dict[str, int]:
        return {"played": 0, "points": 0, "gf": 0, "ga": 0}

    total: dict[int, dict[str, int]] = {}
    home_tbl: dict[int, dict[str, int]] = {}
    away_tbl: dict[int, dict[str, int]] = {}

    def apply(store: dict[int, dict[str, int]], team_id: int, gf: int, ga: int) -> None:
        row = store.setdefault(team_id, blank())
        row["played"] += 1
        row["gf"] += gf
        row["ga"] += ga
        if gf > ga:
            row["points"] += 3
        elif gf == ga:
            row["points"] += 1

    for match in matches:
        if str(match.get("status") or "") != "FINISHED":
            continue
        hid = int((match.get("homeTeam") or {}).get("id") or 0)
        aid = int((match.get("awayTeam") or {}).get("id") or 0)
        score = (match.get("score") or {}).get("fullTime") or {}
        try:
            hs = int(score.get("home"))
            aws = int(score.get("away"))
        except (TypeError, ValueError):
            continue
        apply(total, hid, hs, aws)
        apply(total, aid, aws, hs)
        apply(home_tbl, hid, hs, aws)
        apply(away_tbl, aid, aws, hs)

    def table(store: dict[int, dict[str, int]]) -> list[dict[str, Any]]:
        ranked = sorted(
            store.items(),
            key=lambda item: (-item[1]["points"], -(item[1]["gf"] - item[1]["ga"]), -item[1]["gf"]),
        )
        out: list[dict[str, Any]] = []
        for index, (team_id, row) in enumerate(ranked, start=1):
            out.append(
                {
                    "team": {"id": team_id},
                    "position": index,
                    "playedGames": row["played"],
                    "points": row["points"],
                    "goalDifference": row["gf"] - row["ga"],
                    "goalsFor": row["gf"],
                    "goalsAgainst": row["ga"],
                }
            )
        return out

    return {"TOTAL": table(total), "HOME": table(home_tbl), "AWAY": table(away_tbl)}


def _matches_for_team(matches: list[dict[str, Any]], team_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in matches:
        home_id = int((match.get("homeTeam") or {}).get("id") or 0)
        away_id = int((match.get("awayTeam") or {}).get("id") or 0)
        if home_id == team_id or away_id == team_id:
            out.append(match)
    return out


def _get(client: requests.Session, path: str, params: dict[str, Any] | None = None) -> Any:
    token = football_data_token()
    if not token:
        raise ScoutError("Set FOOTBALL_DATA_TOKEN in .env.")
    last_error: ScoutError | None = None
    for _attempt in range(5):
        response = client.get(
            f"{FOOTBALL_DATA_HOST}{path}",
            params=params,
            headers={"X-Auth-Token": token},
            timeout=30,
        )
        if response.status_code == 429:
            last_error = ScoutError(
                "football-data.org rate limit (free tier is 10 calls/min). Wait a minute and retry."
            )
            time.sleep(7)
            continue
        if response.status_code in {401, 403}:
            raise ScoutError("football-data.org rejected the token. Check FOOTBALL_DATA_TOKEN.")
        response.raise_for_status()
        return response.json()
    raise last_error or ScoutError("football-data.org rate limit (free tier is 10 calls/min). Wait a minute and retry.")


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


def _competition_matches(client: requests.Session, code: str, status: str) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        data = _get(client, f"/competitions/{code}/matches", {"status": status})
        return list(data.get("matches") or [])

    return _cached(f"comp-matches:{code}:{status}", load)


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


def _last_five_scores(team_id: int, matches: list[dict[str, Any]]) -> tuple[tuple[int, int], ...]:
    finished = [m for m in matches if str(m.get("status") or "") == "FINISHED"]
    finished.sort(key=lambda m: str(m.get("utcDate") or ""), reverse=True)
    rows: list[tuple[int, int]] = []
    for match in finished[:5]:
        home_id = int((match.get("homeTeam") or {}).get("id") or 0)
        away_id = int((match.get("awayTeam") or {}).get("id") or 0)
        score = (match.get("score") or {}).get("fullTime") or {}
        try:
            home_goals = int(score.get("home"))
            away_goals = int(score.get("away"))
        except (TypeError, ValueError):
            continue
        if home_id == team_id:
            rows.append((home_goals, away_goals))
        elif away_id == team_id:
            rows.append((away_goals, home_goals))
    return tuple(rows)


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


def _side_ppg(team_id: int, matches: list[dict[str, Any]], home: bool) -> float | None:
    played = 0
    points = 0
    for match in matches:
        if str(match.get("status") or "") != "FINISHED":
            continue
        hid = int((match.get("homeTeam") or {}).get("id") or 0)
        aid = int((match.get("awayTeam") or {}).get("id") or 0)
        score = (match.get("score") or {}).get("fullTime") or {}
        try:
            home_goals = int(score.get("home"))
            away_goals = int(score.get("away"))
        except (TypeError, ValueError):
            continue
        if home and hid == team_id:
            played += 1
            points += 3 if home_goals > away_goals else (1 if home_goals == away_goals else 0)
        elif not home and aid == team_id:
            played += 1
            points += 3 if away_goals > home_goals else (1 if home_goals == away_goals else 0)
    if played <= 0:
        return None
    return round(points / played, 2)


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
        last_five_scores=_last_five_scores(team_id, finished),
        home_ppg=_ppg(home) or _side_ppg(team_id, finished, True),
        away_ppg=_ppg(away) or _side_ppg(team_id, finished, False),
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
