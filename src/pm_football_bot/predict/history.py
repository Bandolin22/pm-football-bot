from __future__ import annotations

from pathlib import Path

import pandas as pd

from pm_football_bot.config import ROOT

DATA_DIR = ROOT / "data" / "predict"
HISTORY_CSV = DATA_DIR / "history.csv"
MODEL_PATH = DATA_DIR / "ensemble.joblib"

BASE_URL = "https://www.football-data.co.uk/mmz4281"

LEAGUE_CODES = {
    "epl": "E0",
    "laliga": "SP1",
    "bundesliga": "D1",
    "seriea": "I1",
    "ligue1": "F1",
}

LEAGUE_LABEL = {
    "epl": "Premier League",
    "laliga": "La Liga",
    "bundesliga": "Bundesliga",
    "seriea": "Serie A",
    "ligue1": "Ligue 1",
}

KEEP = [
    "Date",
    "HomeTeam",
    "AwayTeam",
    "FTHG",
    "FTAG",
    "FTR",
    "HTHG",
    "HTAG",
    "HS",
    "AS",
    "HST",
    "AST",
    "HF",
    "AF",
    "HC",
    "AC",
    "HY",
    "AY",
    "HR",
    "AR",
    "B365H",
    "B365D",
    "B365A",
]


def default_seasons() -> list[str]:
    # 2020/21 … 2025/26. 2026/27 is still thin in August.
    return ["2021", "2122", "2223", "2324", "2425", "2526"]


def download_history(
    seasons: list[str] | None = None,
    leagues: list[str] | None = None,
) -> pd.DataFrame:
    seasons = seasons or default_seasons()
    leagues = leagues or list(LEAGUE_CODES)
    frames: list[pd.DataFrame] = []
    for league in leagues:
        code = LEAGUE_CODES[league]
        for season in seasons:
            url = f"{BASE_URL}/{season}/{code}.csv"
            try:
                raw = pd.read_csv(url, encoding="utf-8", on_bad_lines="skip")
            except UnicodeDecodeError:
                raw = pd.read_csv(url, encoding="latin-1", on_bad_lines="skip")
            except Exception:
                continue
            cols = [c for c in KEEP if c in raw.columns]
            if not {"HomeTeam", "AwayTeam", "FTR"}.issubset(cols):
                continue
            frame = raw[cols].copy()
            frame["league"] = league
            frame["season"] = season
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, ignore_index=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data.to_csv(HISTORY_CSV, index=False)
    return data


def load_history(download: bool = False) -> pd.DataFrame:
    if download or not HISTORY_CSV.exists():
        return download_history()
    return pd.read_csv(HISTORY_CSV)


def clean_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], dayfirst=True, errors="coerce")
    out = out.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTR"]).sort_values("Date")
    numeric = [
        "FTHG",
        "FTAG",
        "HTHG",
        "HTAG",
        "HS",
        "AS",
        "HST",
        "AST",
        "HF",
        "AF",
        "HC",
        "AC",
        "HY",
        "AY",
        "HR",
        "AR",
        "B365H",
        "B365D",
        "B365A",
    ]
    for col in numeric:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["Result"] = out["FTR"].map({"A": 0, "D": 1, "H": 2})
    out = out.dropna(subset=["Result", "FTHG", "FTAG"])
    out["Result"] = out["Result"].astype(int)
    out["FTR_enc"] = out["Result"]
    return out.reset_index(drop=True)
