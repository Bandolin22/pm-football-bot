from __future__ import annotations

import json
from datetime import date, datetime, timezone

from pm_football_bot.watch_rates import (
    RateBucket,
    load_watch_rates,
    overlapping_spl_seasons,
    parse_spl_results_html,
    score_flags,
    season_folders,
    spl_wiki_url,
    watch_of,
)


def test_score_flags_match_keeper_tails():
    assert score_flags(0, 0) == (True, False, False)
    assert score_flags(3, 3) == (False, True, False)
    assert score_flags(4, 0) == (False, False, True)
    assert score_flags(4, 2) == (False, True, True)
    assert score_flags(2, 2) == (False, False, False)


def test_season_folders_cover_rolling_year():
    folders = season_folders(date(2025, 10, 2), date(2026, 10, 2))
    assert "2526" in folders
    assert "2627" in folders


def test_flamengo_rj_maps_to_watchlist():
    assert watch_of("Flamengo RJ") == "Flamengo"


def test_load_watch_rates_from_inline_csv():
    epl = """Date,HomeTeam,AwayTeam,FTHG,FTAG
02/10/2025,Arsenal,Chelsea,0,0
03/10/2025,Liverpool,Burnley,4,0
04/10/2025,Arsenal,Liverpool,3,3
"""
    intl = """date,home_team,away_team,home_score,away_score
2025-11-15,Mexico,Uruguay,0,0
2026-06-14,Germany,Curacao,7,1
"""

    def fetch(url: str) -> str | None:
        if url.endswith("/E0.csv"):
            return epl
        if "international_results" in url:
            return intl
        return None

    report = load_watch_rates(
        days=365,
        now=datetime(2026, 10, 2, tzinfo=timezone.utc),
        fetch_fn=fetch,
    )
    assert report.clash.matches == 3
    assert report.clash.zero == 2
    assert report.clash.over55 == 1
    arsenal = next(row for row in report.teams if row.query == "Arsenal")
    assert arsenal.home.zero == 1
    assert arsenal.clash.matches == 2
    liverpool = next(row for row in report.teams if row.query == "Liverpool")
    assert liverpool.home.other == 1
    assert liverpool.home.over55 == 0
    mexico = next(row for row in report.teams if row.query == "Mexico")
    assert mexico.home.zero == 1
    germany = next(row for row in report.teams if row.query == "Germany")
    assert germany.home.over55 == 1
    assert germany.home.other == 1


def test_rate_bucket_percent():
    bucket = RateBucket()
    bucket.add(0, 0)
    bucket.add(4, 0)
    assert bucket.matches == 2
    assert bucket.zero_rate == 50.0
    assert bucket.other_rate == 50.0
    assert bucket.over55_rate == 0.0


SPL_GRID = """
<table class="wikitable">
<tr><th>Home \\ Away</th><th>AHL</th><th>HIL</th><th>NSR</th><th>QAD</th><th>ITT</th><th>ETT</th><th>FAT</th><th>FAY</th></tr>
<tr><th>Al-Ahli</th><td></td><td>0-0</td><td>1-0</td><td>2-1</td><td>3-1</td><td>4-0</td><td>1-1</td><td>2-2</td></tr>
<tr><th>Al-Hilal</th><td>4-0</td><td></td><td>3-3</td><td>2-0</td><td>1-0</td><td>5-1</td><td>2-1</td><td>0-1</td></tr>
<tr><th>Al-Nassr</th><td>2-1</td><td>1-5</td><td></td><td>3-1</td><td>2-0</td><td>4-2</td><td>3-0</td><td>1-0</td></tr>
<tr><th>Al-Qadsiah</th><td>1-1</td><td>0-2</td><td>1-3</td><td></td><td>0-1</td><td>3-3</td><td>2-0</td><td>1-0</td></tr>
<tr><th>Al-Ittihad</th><td>2-2</td><td>2-3</td><td>1-2</td><td>1-0</td><td></td><td>2-1</td><td>0-0</td><td>3-2</td></tr>
<tr><th>Al-Ettifaq</th><td>0-1</td><td>1-2</td><td>0-3</td><td>1-1</td><td>2-2</td><td></td><td>1-0</td><td>0-0</td></tr>
<tr><th>Al-Fateh</th><td>1-2</td><td>0-4</td><td>1-1</td><td>0-2</td><td>1-3</td><td>2-2</td><td></td><td>1-1</td></tr>
<tr><th>Al-Fayha</th><td>0-2</td><td>1-1</td><td>0-1</td><td>1-2</td><td>2-2</td><td>1-1</td><td>0-0</td><td></td></tr>
</table>
"""


def test_overlapping_spl_seasons_for_rolling_year():
    seasons = overlapping_spl_seasons(date(2025, 10, 13), date(2026, 10, 13))
    assert 2025 in seasons
    assert 2026 in seasons
    assert 2024 not in seasons


def test_parse_spl_results_html_reads_watchlist_grid():
    games = parse_spl_results_html(SPL_GRID)
    assert ("Al-Ahli", "Al-Hilal", 0, 0) in games
    assert ("Al-Hilal", "Al-Ahli", 4, 0) in games
    assert ("Al-Nassr", "Al-Hilal", 1, 5) in games
    assert len(games) == 56


def test_saudi_clubs_map_from_wikipedia_names():
    assert watch_of("Al-Hilal") == "Al Hilal"
    assert watch_of("Al-Nassr") == "Al Nassr"
    assert watch_of("Al-Ahli") == "Al Ahli"
    assert watch_of("Al-Qadsiah") == "Al Qadisiyah"
    assert watch_of("Al-Ittihad") == "Al Ittihad"
    assert watch_of("Al-Ettifaq") is None


def test_load_watch_rates_includes_saudi_all_matches():
    payload = '{"parse": {"text": {"*": %s}}}' % json.dumps(SPL_GRID)

    def fetch(url: str) -> str | None:
        if "Saudi_Pro_League" in url and "2025" in url:
            return payload
        return None

    report = load_watch_rates(
        days=365,
        now=datetime(2026, 10, 13, tzinfo=timezone.utc),
        fetch_fn=fetch,
    )
    hilal = next(row for row in report.teams if row.query == "Al Hilal")
    nassr = next(row for row in report.teams if row.query == "Al Nassr")
    ahli = next(row for row in report.teams if row.query == "Al Ahli")
    assert hilal.home.combined(hilal.away).matches == 14
    assert ahli.home.zero == 1
    assert hilal.home.other == 2
    assert hilal.home.over55 == 2
    assert nassr.away.over55 == 1
    assert spl_wiki_url(2025).endswith("format=json")
    assert any("Saudi Pro League 2025-26" in item for item in report.sources)
