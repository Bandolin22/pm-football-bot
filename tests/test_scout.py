from pm_football_bot.scout import (
    TeamPulse,
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


def test_load_briefing_without_token_explains_setup(monkeypatch):
    monkeypatch.setattr("pm_football_bot.scout.football_data_token", lambda: None)
    briefing = load_briefing(
        "epl",
        "Hull City AFC vs. Manchester United FC",
        None,
        "Manchester United FC",
    )
    assert briefing.error
    assert "FOOTBALL_DATA_TOKEN" in briefing.error
