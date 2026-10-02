from __future__ import annotations

from datetime import date, datetime, timezone

from pm_football_bot.watch_rates import RateBucket, load_watch_rates, score_flags, season_folders, watch_of


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
