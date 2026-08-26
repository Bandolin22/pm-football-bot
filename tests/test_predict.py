from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from pm_football_bot.predict.features import build_features, inference_row, team_snapshots
from pm_football_bot.predict.fusion import blend_probabilities, polymarket_1x2
from pm_football_bot.predict.history import clean_history
from pm_football_bot.predict.names import best_key, canon_name


def _synthetic(n_matchdays: int = 36, seed: int = 0) -> pd.DataFrame:
    teams = ["Arsenal", "Chelsea", "Liverpool", "Everton", "Fulham", "Brentford"]
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    date = pd.Timestamp("2022-08-06")
    for _ in range(n_matchdays):
        order = rng.permutation(teams)
        for i in range(0, len(order), 2):
            home, away = str(order[i]), str(order[i + 1])
            fthg = int(rng.integers(0, 4))
            ftag = int(rng.integers(0, 4))
            hs = int(rng.integers(6, 22))
            astats = int(rng.integers(6, 22))
            hst = min(hs, int(rng.integers(1, 10)))
            ast = min(astats, int(rng.integers(1, 10)))
            ftr = "H" if fthg > ftag else "A" if fthg < ftag else "D"
            rows.append(
                {
                    "Date": date.strftime("%d/%m/%Y"),
                    "HomeTeam": home,
                    "AwayTeam": away,
                    "FTHG": fthg,
                    "FTAG": ftag,
                    "FTR": ftr,
                    "HTHG": min(fthg, 1),
                    "HTAG": min(ftag, 1),
                    "HS": hs,
                    "AS": astats,
                    "HST": hst,
                    "AST": ast,
                    "HF": 10,
                    "AF": 10,
                    "HC": int(rng.integers(2, 10)),
                    "AC": int(rng.integers(2, 10)),
                    "HY": 1,
                    "AY": 1,
                    "HR": 0,
                    "AR": 0,
                    "B365H": 2.1 if ftr != "A" else 3.4,
                    "B365D": 3.4,
                    "B365A": 3.2 if ftr != "H" else 2.2,
                    "league": "epl",
                    "season": "2223",
                }
            )
        date += pd.Timedelta(days=7)
    return pd.DataFrame(rows)


def test_canon_and_best_key():
    assert canon_name("Man United") == "manchester united"
    keys = ["Man United", "Chelsea", "Ath Madrid"]
    assert best_key("Manchester United FC", keys) == "Man United"
    assert best_key("Atletico Madrid", keys) == "Ath Madrid"
    assert best_key("Random FC", keys) is None


def test_polymarket_draw_is_residual():
    home, draw, away = polymarket_1x2(0.62, 0.22)
    assert abs(home + draw + away - 1.0) < 1e-9
    assert abs(draw - 0.16) < 1e-9


def test_blend_ml_and_poly_without_book():
    blended = blend_probabilities((0.50, 0.25, 0.25), (0.40, 0.30, 0.30), None)
    assert blended.sources == ("ml", "poly")
    assert abs(blended.weights["ml"] - 0.55) < 1e-9
    assert abs(blended.home - (0.55 * 0.50 + 0.45 * 0.40)) < 1e-9
    assert abs(blended.home + blended.draw + blended.away - 1.0) < 1e-9


def test_blend_three_sources():
    blended = blend_probabilities((0.5, 0.25, 0.25), (0.4, 0.3, 0.3), (0.45, 0.27, 0.28))
    assert set(blended.sources) == {"ml", "poly", "book"}
    expected = 0.45 * 0.5 + 0.35 * 0.4 + 0.20 * 0.45
    assert abs(blended.home - expected) < 1e-9


def test_rolling_features_exclude_current_match():
    cleaned = clean_history(_synthetic())
    featured = build_features(cleaned)
    team = "Arsenal"
    prior: list[int] = []
    for _, row in featured.sort_values("Date").iterrows():
        if row["HomeTeam"] != team and row["AwayTeam"] != team:
            continue
        is_home = row["HomeTeam"] == team
        goals = int(row["FTHG"] if is_home else row["FTAG"])
        avg = row["home_avg_GF" if is_home else "away_avg_GF"]
        if len(prior) < 3:
            assert pd.isna(avg)
        else:
            expected = float(np.mean(prior[-5:]))
            assert abs(float(avg) - expected) < 1e-9
        prior.append(goals)


def test_elo_is_recorded_before_the_result():
    cleaned = clean_history(_synthetic(n_matchdays=8))
    featured = build_features(cleaned)
    first = featured.sort_values("Date").iloc[0]
    assert first["elo_home"] == 1500.0
    assert first["elo_away"] == 1500.0
    assert first["elo_home_post"] != first["elo_home"] or int(first["FTHG"]) == int(first["FTAG"])


def test_snapshots_use_post_match_elo():
    cleaned = clean_history(_synthetic(n_matchdays=12))
    featured = build_features(cleaned)
    snaps = team_snapshots(featured)
    team = "Arsenal"
    last = featured[(featured["HomeTeam"] == team) | (featured["AwayTeam"] == team)].sort_values("Date").iloc[-1]
    if last["HomeTeam"] == team:
        assert abs(float(snaps[team]["elo"]) - float(last["elo_home_post"])) < 1e-6
    else:
        assert abs(float(snaps[team]["elo"]) - float(last["elo_away_post"])) < 1e-6


def test_inference_row_has_ml_columns():
    cleaned = clean_history(_synthetic())
    featured = build_features(cleaned)
    snaps = team_snapshots(featured)
    row = inference_row(
        featured,
        "Arsenal",
        "Chelsea",
        datetime(2023, 6, 1, tzinfo=timezone.utc),
        snaps,
    )
    assert "elo_diff" in row.columns
    assert "home_avg_GF" in row.columns
    assert float(row["home_rest_days"].iloc[0]) >= 0


def test_train_and_forecast(tmp_path, monkeypatch):
    from pm_football_bot.predict import engine as engine_mod
    from pm_football_bot.predict import history as history_mod
    from pm_football_bot.predict import model as model_mod
    from pm_football_bot.predict.engine import clear_cache, forecast_match, model_ready
    from pm_football_bot.predict.model import train_ensemble

    cleaned = clean_history(_synthetic(n_matchdays=48))
    featured = build_features(cleaned)
    model_path = tmp_path / "ensemble.joblib"
    history_path = tmp_path / "history.csv"
    cleaned.to_csv(history_path, index=False)
    monkeypatch.setattr(history_mod, "MODEL_PATH", model_path)
    monkeypatch.setattr(history_mod, "HISTORY_CSV", history_path)
    monkeypatch.setattr(history_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(model_mod, "MODEL_PATH", model_path)
    monkeypatch.setattr(model_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(engine_mod, "MODEL_PATH", model_path)
    monkeypatch.setattr(engine_mod, "HISTORY_CSV", history_path)

    stats = train_ensemble(featured, min_train_rows=40)
    assert stats["rows_train"] >= 40
    assert 0.0 <= stats["accuracy"] <= 1.0
    assert model_ready()

    def fake_history(download: bool = False):
        return pd.read_csv(history_path)

    monkeypatch.setattr(engine_mod, "load_history", fake_history)
    clear_cache()
    forecast = forecast_match(
        "Arsenal vs. Chelsea",
        favorite_team="Arsenal",
        favorite_yes=0.58,
        dog_yes=0.22,
        kickoff=datetime(2023, 6, 1, tzinfo=timezone.utc),
    )
    assert forecast.error is None
    assert forecast.ml is not None
    assert forecast.poly is not None
    assert forecast.blended is not None
    assert abs(sum(forecast.ml) - 1.0) < 1e-6
    assert forecast.pick in {"Home", "Draw", "Away"}
