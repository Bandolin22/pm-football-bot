from pm_football_bot.scout import (
    ScoutError,
    Briefing,
    TeamPulse,
    _csv_when,
    _last_five_scores,
    fold_name,
    load_briefing,
    match_team,
    split_fixture,
    veto_notes,
)


def test_splits_polymarket_titles():
    assert split_fixture("Hull City AFC vs. Manchester United FC") == (
        "Hull City AFC",
        "Manchester United FC",
    )
    assert split_fixture("Elche CF vs FC Barcelona") == ("Elche CF", "FC Barcelona")


def test_folds_club_suffixes():
    assert fold_name("Hull City AFC") == "hull city"
    assert fold_name("Manchester United FC") == "manchester united"
    assert fold_name("FC Bayern München") == "bayern munchen"
    assert fold_name("Paris Saint-Germain FC") == "paris saint germain"
    assert fold_name("Club Brugge") == "brugge"
    assert fold_name("Fenerbahçe") == "fenerbahce"
    assert fold_name("FK Bodø/Glimt") == "fk bodo glimt"
    assert fold_name("Barça") == "barcelona"
    assert fold_name("Barca") == "barcelona"
    assert fold_name("Man City") == "manchester city"
    assert fold_name("Paris SG") == "paris saint germain"
    assert fold_name("Sporting CP") == "sporting"
    assert fold_name("Sp Lisbon") == "sporting"


def test_matches_football_data_team_rows():
    teams = [
        {"id": 66, "name": "Manchester United FC", "shortName": "Man United", "tla": "MUN"},
        {"id": 322, "name": "Hull City FC", "shortName": "Hull", "tla": "HUL"},
        {"id": 5, "name": "FC Bayern München", "shortName": "Bayern", "tla": "FCB"},
        {"id": 108, "name": "FC Internazionale Milano", "shortName": "Inter", "tla": "INT"},
        {"id": 77, "name": "Athletic Club", "shortName": "Athletic", "tla": "ATH"},
    ]
    assert match_team("Manchester United FC", teams)["id"] == 66
    assert match_team("Hull City AFC", teams)["id"] == 322
    assert match_team("Bayern Munich", teams)["id"] == 5
    assert match_team("Inter Milan", teams)["id"] == 108
    assert match_team("Athletic Club", teams)["id"] == 77
    assert match_team("Random FC", teams) is None
    espn = [
        {"id": 382, "name": "Manchester City", "shortName": "Man City", "tla": "MNC", "location": "Manchester City"},
        {"id": 366, "name": "Sunderland", "shortName": "Sunderland", "tla": "SUN", "location": "Sunderland"},
    ]
    assert match_team("Manchester City FC", espn)["id"] == 382
    assert match_team("Sunderland AFC", espn)["id"] == 366


def test_last_five_scores_uses_team_id_not_short_name():
    matches = [
        {
            "status": "FINISHED",
            "utcDate": "2026-09-18T19:00:00Z",
            "homeTeam": {"id": 81, "shortName": "Barça"},
            "awayTeam": {"id": 89, "shortName": "Santander"},
            "score": {"fullTime": {"home": 7, "away": 2}},
        },
        {
            "status": "FINISHED",
            "utcDate": "2026-09-14T19:00:00Z",
            "homeTeam": {"id": 88, "shortName": "Levante"},
            "awayTeam": {"id": 81, "shortName": "Barça"},
            "score": {"fullTime": {"home": 2, "away": 4}},
        },
    ]
    assert _last_five_scores(81, matches) == ((7, 2), (4, 2))


def _pulse(**kwargs) -> TeamPulse:
    base = dict(
        name="x",
        position=10,
        played=3,
        points=3,
        goal_diff=0,
        form="WDL",
        last_five=(),
        home_ppg=1.0,
        away_ppg=1.0,
        gf_pg=1.0,
        ga_pg=1.0,
        rest_days=6,
        next_match=None,
    )
    base.update(kwargs)
    return TeamPulse(**base)


def test_veto_flags_weak_away_favorite():
    home = _pulse(name="Hull City FC", home_ppg=1.8, away_ppg=0.5, form="WWD")
    away = _pulse(name="Manchester United FC", home_ppg=2.0, away_ppg=0.4, form="LDL")
    notes = veto_notes(home, away, "Manchester United FC", "Hull City AFC", "Manchester United FC")
    assert any("Skip fade_dog" in row for row in notes)


def test_no_veto_for_home_fortress_favorite():
    home = _pulse(name="FC Barcelona", home_ppg=2.4, away_ppg=1.8, form="WWW")
    away = _pulse(name="Elche CF", home_ppg=0.8, away_ppg=0.4, form="LLD")
    notes = veto_notes(home, away, "FC Barcelona", "FC Barcelona", "Elche CF")
    assert notes == ()


def test_load_briefing_uses_results_csv_without_token(monkeypatch):
    monkeypatch.setattr("pm_football_bot.scout.football_data_token", lambda: None)

    def fake_csv(*_args, **_kwargs):
        return Briefing(
            "Manchester City FC",
            "Sunderland AFC",
            None,
            None,
            (),
            (),
            sources=("football-data.co.uk",),
        )

    monkeypatch.setattr("pm_football_bot.scout._briefing_from_results_csv", fake_csv)
    briefing = load_briefing(
        "epl",
        "Manchester City FC vs. Sunderland AFC",
        None,
        "Manchester City FC",
    )
    assert briefing.error is None
    assert briefing.sources == ("football-data.co.uk",)


def test_load_briefing_falls_back_to_football_data(monkeypatch):
    monkeypatch.setattr("pm_football_bot.scout.football_data_token", lambda: "token")
    monkeypatch.setattr(
        "pm_football_bot.scout._briefing_from_results_csv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ScoutError("csv down")),
    )

    def fake_fd(*_args, **_kwargs):
        return Briefing("Hull City AFC", "Manchester United FC", None, None, (), (), sources=("football-data.org",))

    monkeypatch.setattr("pm_football_bot.scout._briefing_from_football_data", fake_fd)
    briefing = load_briefing(
        "epl",
        "Hull City AFC vs. Manchester United FC",
        None,
        "Manchester United FC",
    )
    assert briefing.sources == ("football-data.org",)


def test_csv_when_parses_uk_dates():
    assert _csv_when("14/09/2026", "20:00").startswith("2026-09-14T20:00:00")
