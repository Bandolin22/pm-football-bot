from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, VotingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pm_football_bot.predict.features import ml_feature_columns
from pm_football_bot.predict.history import DATA_DIR, MODEL_PATH

RANDOM_STATE = 42
HOLDOUT_FRACTION = 0.20
MIN_TRAIN_ROWS = 800


def _pipeline() -> Pipeline:
    estimators = [
        (
            "lr",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                    ("clf", LogisticRegression(max_iter=800, C=0.4, random_state=RANDOM_STATE)),
                ]
            ),
        ),
        (
            "rf",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    (
                        "clf",
                        RandomForestClassifier(
                            n_estimators=180,
                            max_depth=8,
                            min_samples_leaf=12,
                            random_state=RANDOM_STATE,
                            n_jobs=1,
                        ),
                    ),
                ]
            ),
        ),
        (
            "gb",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    (
                        "clf",
                        GradientBoostingClassifier(
                            n_estimators=120,
                            max_depth=3,
                            learning_rate=0.06,
                            random_state=RANDOM_STATE,
                        ),
                    ),
                ]
            ),
        ),
    ]
    return VotingClassifier(estimators=estimators, voting="soft")


def chronological_split(df: pd.DataFrame, holdout: float = HOLDOUT_FRACTION) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("Date")
    cut = int(len(ordered) * (1.0 - holdout))
    return ordered.iloc[:cut], ordered.iloc[cut:]


def train_ensemble(featured: pd.DataFrame, min_train_rows: int = MIN_TRAIN_ROWS) -> dict:
    cols = ml_feature_columns(featured)
    needed = ["FTR_enc"]
    if "home_Form" in featured.columns:
        needed.extend(["home_Form", "away_Form"])
    usable = featured.dropna(subset=needed).copy()
    train, test = chronological_split(usable)
    if len(train) < min_train_rows:
        raise ValueError(f"Need at least {min_train_rows} training rows, got {len(train)}")

    x_train = train[cols]
    y_train = train["FTR_enc"].astype(int)
    x_test = test[cols]
    y_test = test["FTR_enc"].astype(int)

    model = _pipeline()
    model.fit(x_train, y_train)
    proba = model.predict_proba(x_test)
    classes = list(model.classes_)
    pred = np.array(classes)[proba.argmax(axis=1)]
    acc = float((pred == y_test.to_numpy()).mean())
    logloss = _log_loss(y_test.to_numpy(), proba, classes)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "columns": cols, "classes": classes}, MODEL_PATH)
    return {
        "rows_train": int(len(train)),
        "rows_test": int(len(test)),
        "accuracy": acc,
        "log_loss": logloss,
        "columns": cols,
        "path": str(MODEL_PATH),
        "baseline_home_rate": float((y_test == 2).mean()),
    }


def load_ensemble(path: Path | None = None) -> dict:
    bundle = joblib.load(path or MODEL_PATH)
    if "model" not in bundle or "columns" not in bundle:
        raise ValueError("Invalid model file")
    return bundle


def predict_proba(bundle: dict, row: pd.DataFrame) -> tuple[float, float, float]:
    model = bundle["model"]
    cols = bundle["columns"]
    classes = list(bundle.get("classes") or model.classes_)
    missing = [c for c in cols if c not in row.columns]
    frame = row.copy()
    for col in missing:
        frame[col] = np.nan
    proba = model.predict_proba(frame[cols])[0]
    mapped = {int(cls): float(p) for cls, p in zip(classes, proba)}
    away = mapped.get(0, 0.0)
    draw = mapped.get(1, 0.0)
    home = mapped.get(2, 0.0)
    total = home + draw + away
    if total <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return home / total, draw / total, away / total


def _log_loss(y_true: np.ndarray, proba: np.ndarray, classes: list) -> float:
    idx = {int(c): i for i, c in enumerate(classes)}
    eps = 1e-15
    losses = []
    for yt, row in zip(y_true, proba):
        p = float(row[idx[int(yt)]])
        losses.append(-np.log(min(1 - eps, max(eps, p))))
    return float(np.mean(losses))
