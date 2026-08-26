from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd

WINDOW = 5
MIN_PERIODS = 3
ELO_K = 32.0
ELO_HOME_ADV = 65.0

TEAM_STATS = [
    "GF",
    "GA",
    "Shots",
    "ShotsAgainst",
    "SoT",
    "SoTAgainst",
    "Corners",
    "CornersAgainst",
]


def add_bookmaker_probs(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if not {"B365H", "B365D", "B365A"}.issubset(out.columns):
        return out
    for col in ("B365H", "B365D", "B365A"):
        out.loc[out[col] <= 1.0, col] = np.nan
    raw_h = 1.0 / out["B365H"]
    raw_d = 1.0 / out["B365D"]
    raw_a = 1.0 / out["B365A"]
    total = raw_h + raw_d + raw_a
    out["book_H"] = raw_h / total
    out["book_D"] = raw_d / total
    out["book_A"] = raw_a / total
    out["book_spread"] = out["book_H"] - out["book_A"]
    out["book_overround"] = total - 1.0
    return out


def add_rolling_features(df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    rows = _team_rows(df)
    for col in TEAM_STATS:
        rows[f"avg_{col}"] = rows.groupby("Team", sort=False)[col].transform(
            lambda s: s.shift(1).rolling(window, min_periods=MIN_PERIODS).mean()
        )
    rows["Points"] = np.select([rows["GF"] > rows["GA"], rows["GF"] == rows["GA"]], [3, 1], default=0)
    rows["Form"] = rows.groupby("Team", sort=False)["Points"].transform(
        lambda s: s.shift(1).rolling(window, min_periods=MIN_PERIODS).mean()
    )
    roll = [f"avg_{c}" for c in TEAM_STATS] + ["Form"]
    home = rows.loc[rows["IsHome"] == 1, ["MatchIndex", *roll]].set_index("MatchIndex").add_prefix("home_")
    away = rows.loc[rows["IsHome"] == 0, ["MatchIndex", *roll]].set_index("MatchIndex").add_prefix("away_")
    out = df.join(home, how="left").join(away, how="left")
    for col in roll:
        out[f"diff_{col}"] = out[f"home_{col}"] - out[f"away_{col}"]
    return out


def add_elo(df: pd.DataFrame, k: float = ELO_K, home_adv: float = ELO_HOME_ADV) -> pd.DataFrame:
    ordered = df.sort_values("Date").copy()
    ratings: dict[str, float] = {}
    records: list[dict[str, float]] = []

    def rating(team: str) -> float:
        return ratings.setdefault(team, 1500.0)

    def expected(a: float, b: float) -> float:
        return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))

    for _, row in ordered.iterrows():
        home, away = row["HomeTeam"], row["AwayTeam"]
        rh, ra = rating(home), rating(away)
        eh = expected(rh + home_adv, ra)
        records.append(
            {
                "elo_home": rh,
                "elo_away": ra,
                "elo_diff": rh - ra,
                "elo_expected_home": eh,
            }
        )
        hg, ag = int(row["FTHG"]), int(row["FTAG"])
        if hg > ag:
            sh, sa = 1.0, 0.0
        elif hg < ag:
            sh, sa = 0.0, 1.0
        else:
            sh, sa = 0.5, 0.5
        margin = 1.0 if hg == ag else max(1.0, float(np.log(abs(hg - ag) + 1)))
        new_h = rh + k * margin * (sh - eh)
        new_a = ra + k * margin * (sa - (1.0 - eh))
        records[-1]["elo_home_post"] = new_h
        records[-1]["elo_away_post"] = new_a
        ratings[home] = new_h
        ratings[away] = new_a

    return pd.concat([ordered.reset_index(drop=True), pd.DataFrame(records)], axis=1)


def add_xg_proxy(df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    if not {"HS", "AS", "HST", "AST"}.issubset(df.columns):
        return df.copy()
    sot, other = 0.30, 0.03
    home = pd.DataFrame(
        {
            "MatchIndex": df.index,
            "Date": df["Date"],
            "Team": df["HomeTeam"],
            "xg_for": df["HST"] * sot + (df["HS"] - df["HST"]).clip(lower=0) * other,
            "xg_against": df["AST"] * sot + (df["AS"] - df["AST"]).clip(lower=0) * other,
            "goals": df["FTHG"],
            "IsHome": 1,
        }
    )
    away = pd.DataFrame(
        {
            "MatchIndex": df.index,
            "Date": df["Date"],
            "Team": df["AwayTeam"],
            "xg_for": df["AST"] * sot + (df["AS"] - df["AST"]).clip(lower=0) * other,
            "xg_against": df["HST"] * sot + (df["HS"] - df["HST"]).clip(lower=0) * other,
            "goals": df["FTAG"],
            "IsHome": 0,
        }
    )
    rows = pd.concat([home, away], ignore_index=True).sort_values(["Date", "MatchIndex"])
    rows["xg_over"] = rows["goals"] - rows["xg_for"]
    for col in ("xg_for", "xg_against", "xg_over"):
        rows[f"{col}_{window}"] = rows.groupby("Team", sort=False)[col].transform(
            lambda s: s.shift(1).rolling(window, min_periods=MIN_PERIODS).mean()
        )
    feats = [f"xg_for_{window}", f"xg_against_{window}", f"xg_over_{window}"]
    h = rows.loc[rows["IsHome"] == 1, ["MatchIndex", *feats]].set_index("MatchIndex").add_prefix("home_")
    a = rows.loc[rows["IsHome"] == 0, ["MatchIndex", *feats]].set_index("MatchIndex").add_prefix("away_")
    out = df.join(h).join(a)
    for col in feats:
        out[f"diff_{col}"] = out[f"home_{col}"] - out[f"away_{col}"]
    return out


def add_rest(df: pd.DataFrame) -> pd.DataFrame:
    ordered = df.sort_values("Date").copy()
    last: dict[str, pd.Timestamp] = {}
    home_rest: list[int] = []
    away_rest: list[int] = []
    for _, row in ordered.iterrows():
        date = row["Date"]
        home, away = row["HomeTeam"], row["AwayTeam"]
        hd = (date - last[home]).days if home in last else 14
        ad = (date - last[away]).days if away in last else 14
        home_rest.append(max(0, min(int(hd), 30)))
        away_rest.append(max(0, min(int(ad), 30)))
        last[home] = date
        last[away] = date
    ordered["home_rest_days"] = home_rest
    ordered["away_rest_days"] = away_rest
    ordered["rest_advantage"] = ordered["home_rest_days"] - ordered["away_rest_days"]
    ordered["home_fatigued"] = (ordered["home_rest_days"] <= 3).astype(int)
    ordered["away_fatigued"] = (ordered["away_rest_days"] <= 3).astype(int)
    ordered["is_midweek"] = ordered["Date"].dt.dayofweek.isin([1, 2]).astype(int)
    return ordered


def add_h2h(df: pd.DataFrame, n_last: int = 5) -> pd.DataFrame:
    ordered = df.sort_values("Date").copy()
    hist: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=n_last))
    rows: list[dict[str, float]] = []
    for _, row in ordered.iterrows():
        home, away = row["HomeTeam"], row["AwayTeam"]
        key = tuple(sorted((home, away)))
        prev = list(hist[key])
        if len(prev) < 2:
            rows.append({"h2h_home_wins": np.nan, "h2h_draws": np.nan, "h2h_goals_avg": np.nan})
        else:
            wins = draws = goals = 0
            for match in prev:
                goals += match["hg"] + match["ag"]
                if match["hg"] == match["ag"]:
                    draws += 1
                elif match["home"] == home:
                    wins += int(match["hg"] > match["ag"])
                else:
                    wins += int(match["ag"] > match["hg"])
            n = len(prev)
            rows.append(
                {
                    "h2h_home_wins": wins / n,
                    "h2h_draws": draws / n,
                    "h2h_goals_avg": goals / n,
                }
            )
        hist[key].append(
            {"home": home, "away": away, "hg": int(row["FTHG"]), "ag": int(row["FTAG"])}
        )
    return pd.concat([ordered.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = add_bookmaker_probs(df)
    out = add_rolling_features(out)
    out = add_elo(out)
    out = add_xg_proxy(out)
    out = add_rest(out)
    out = add_h2h(out)
    return out.reset_index(drop=True)


def team_snapshots(df: pd.DataFrame) -> dict[str, dict[str, float | str]]:
    """State after the last completed match = pre-match features for the next one."""
    rows = _team_rows(df)
    sot, other = 0.30, 0.03
    rows["xg_for"] = rows["SoT"] * sot + (rows["Shots"] - rows["SoT"]).clip(lower=0) * other
    rows["xg_against"] = rows["SoTAgainst"] * sot + (rows["ShotsAgainst"] - rows["SoTAgainst"]).clip(lower=0) * other
    rows["xg_over"] = rows["GF"] - rows["xg_for"]
    for col in TEAM_STATS:
        rows[f"avg_{col}"] = rows.groupby("Team", sort=False)[col].transform(
            lambda s: s.rolling(WINDOW, min_periods=MIN_PERIODS).mean()
        )
    for col in ("xg_for", "xg_against", "xg_over"):
        rows[f"{col}_{WINDOW}"] = rows.groupby("Team", sort=False)[col].transform(
            lambda s: s.rolling(WINDOW, min_periods=MIN_PERIODS).mean()
        )
    rows["Points"] = np.select([rows["GF"] > rows["GA"], rows["GF"] == rows["GA"]], [3, 1], default=0)
    rows["Form"] = rows.groupby("Team", sort=False)["Points"].transform(
        lambda s: s.rolling(WINDOW, min_periods=MIN_PERIODS).mean()
    )
    last = rows.sort_values("Date").groupby("Team", sort=False).tail(1)
    snaps: dict[str, dict[str, float | str]] = {}
    for _, row in last.iterrows():
        team = str(row["Team"])
        snap = {f"avg_{c}": _num(row[f"avg_{c}"], 0.0) for c in TEAM_STATS}
        snap["Form"] = _num(row["Form"], 1.0)
        for col in (f"xg_for_{WINDOW}", f"xg_against_{WINDOW}", f"xg_over_{WINDOW}"):
            snap[col] = _num(row[col], 0.0)
        snap["elo"] = _latest_elo(df, team)
        snap["last_date"] = pd.Timestamp(row["Date"]).isoformat()
        snaps[team] = snap
    return snaps


def inference_row(
    featured: pd.DataFrame,
    home: str,
    away: str,
    kickoff: pd.Timestamp | None,
    snaps: dict[str, dict[str, float | str]] | None = None,
) -> pd.DataFrame:
    snaps = snaps or team_snapshots(featured)
    home_s = snaps[home]
    away_s = snaps[away]
    kick = _naive_ts(kickoff)
    home_rest = _rest_days(home_s.get("last_date"), kick)
    away_rest = _rest_days(away_s.get("last_date"), kick)
    rh = float(home_s["elo"])
    ra = float(away_s["elo"])
    payload: dict[str, float] = {
        "home_Form": float(home_s["Form"]),
        "away_Form": float(away_s["Form"]),
        "diff_Form": float(home_s["Form"]) - float(away_s["Form"]),
        "elo_home": rh,
        "elo_away": ra,
        "elo_diff": rh - ra,
        "elo_expected_home": 1.0 / (1.0 + 10 ** ((ra - (rh + ELO_HOME_ADV)) / 400.0)),
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "rest_advantage": home_rest - away_rest,
        "home_fatigued": int(home_rest <= 3),
        "away_fatigued": int(away_rest <= 3),
        "is_midweek": int(kick.dayofweek in (1, 2)),
    }
    for col in TEAM_STATS:
        payload[f"home_avg_{col}"] = float(home_s[f"avg_{col}"])
        payload[f"away_avg_{col}"] = float(away_s[f"avg_{col}"])
        payload[f"diff_avg_{col}"] = payload[f"home_avg_{col}"] - payload[f"away_avg_{col}"]
    for col in (f"xg_for_{WINDOW}", f"xg_against_{WINDOW}", f"xg_over_{WINDOW}"):
        payload[f"home_{col}"] = float(home_s[col])
        payload[f"away_{col}"] = float(away_s[col])
        payload[f"diff_{col}"] = payload[f"home_{col}"] - payload[f"away_{col}"]
    payload.update(h2h_before(featured, home, away, kick))
    return pd.DataFrame([payload])


def h2h_before(
    df: pd.DataFrame,
    home: str,
    away: str,
    before: pd.Timestamp,
    n_last: int = 5,
) -> dict[str, float]:
    mask = ((df["HomeTeam"] == home) & (df["AwayTeam"] == away)) | (
        (df["HomeTeam"] == away) & (df["AwayTeam"] == home)
    )
    prev = df.loc[mask & (df["Date"] < before)].sort_values("Date").tail(n_last)
    if len(prev) < 2:
        return {"h2h_home_wins": np.nan, "h2h_draws": np.nan, "h2h_goals_avg": np.nan}
    wins = draws = goals = 0
    for _, row in prev.iterrows():
        hg, ag = int(row["FTHG"]), int(row["FTAG"])
        goals += hg + ag
        if hg == ag:
            draws += 1
        elif row["HomeTeam"] == home:
            wins += int(hg > ag)
        else:
            wins += int(ag > hg)
    n = len(prev)
    return {"h2h_home_wins": wins / n, "h2h_draws": draws / n, "h2h_goals_avg": goals / n}


def ml_feature_columns(df: pd.DataFrame) -> list[str]:
    prefixes = ("home_avg_", "away_avg_", "diff_avg_", "home_xg_", "away_xg_", "diff_xg_")
    exact = {
        "home_Form",
        "away_Form",
        "diff_Form",
        "elo_home",
        "elo_away",
        "elo_diff",
        "elo_expected_home",
        "home_rest_days",
        "away_rest_days",
        "rest_advantage",
        "home_fatigued",
        "away_fatigued",
        "is_midweek",
        "h2h_home_wins",
        "h2h_draws",
        "h2h_goals_avg",
    }
    return [c for c in df.columns if c.startswith(prefixes) or c in exact]


def _team_rows(df: pd.DataFrame) -> pd.DataFrame:
    home = df[["HomeTeam", "FTHG", "FTAG", "HS", "AS", "HST", "AST", "HC", "AC"]].copy()
    home.insert(0, "MatchIndex", df.index)
    home.insert(1, "Date", df["Date"])
    home.columns = [
        "MatchIndex",
        "Date",
        "Team",
        "GF",
        "GA",
        "Shots",
        "ShotsAgainst",
        "SoT",
        "SoTAgainst",
        "Corners",
        "CornersAgainst",
    ]
    home["IsHome"] = 1
    away = df[["AwayTeam", "FTAG", "FTHG", "AS", "HS", "AST", "HST", "AC", "HC"]].copy()
    away.insert(0, "MatchIndex", df.index)
    away.insert(1, "Date", df["Date"])
    away.columns = home.columns[:-1]
    away["IsHome"] = 0
    return pd.concat([home, away], ignore_index=True).sort_values(["Date", "MatchIndex"])


def _naive_ts(value) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="UTC").tz_localize(None)
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        return stamp.tz_convert("UTC").tz_localize(None)
    return stamp


def _num(value, default: float) -> float:
    return float(value) if pd.notna(value) else default


def _rest_days(last_iso, kick: pd.Timestamp) -> int:
    if last_iso is None or (isinstance(last_iso, float) and np.isnan(last_iso)):
        return 14
    last = pd.Timestamp(last_iso)
    if last.tzinfo is not None and kick.tzinfo is None:
        kick = kick.tz_localize(last.tzinfo)
    elif last.tzinfo is None and kick.tzinfo is not None:
        last = last.tz_localize(kick.tzinfo)
    return max(0, min(int((kick - last).days), 30))


def _latest_elo(df: pd.DataFrame, team: str) -> float:
    parts: list[pd.DataFrame] = []
    if "elo_home_post" in df.columns:
        parts.append(
            df.loc[df["HomeTeam"] == team, ["Date", "elo_home_post"]].rename(columns={"elo_home_post": "elo"})
        )
    if "elo_away_post" in df.columns:
        parts.append(
            df.loc[df["AwayTeam"] == team, ["Date", "elo_away_post"]].rename(columns={"elo_away_post": "elo"})
        )
    if not parts:
        return 1500.0
    both = pd.concat(parts, ignore_index=True).dropna(subset=["elo"]).sort_values("Date")
    if both.empty:
        return 1500.0
    return float(both["elo"].iloc[-1])
