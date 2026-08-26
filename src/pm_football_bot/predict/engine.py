from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from difflib import SequenceMatcher

import pandas as pd

from pm_football_bot.predict.features import build_features, inference_row, team_snapshots
from pm_football_bot.predict.fusion import Blend, blend_probabilities, polymarket_1x2
from pm_football_bot.predict.history import HISTORY_CSV, MODEL_PATH, clean_history, load_history
from pm_football_bot.predict.model import load_ensemble, predict_proba
from pm_football_bot.predict.names import best_key
from pm_football_bot.scout import fold_name, split_fixture

_STORE: dict = {}


@dataclass(frozen=True)
class Forecast:
    home_name: str
    away_name: str
    matched_home: str | None = None
    matched_away: str | None = None
    ml: tuple[float, float, float] | None = None
    poly: tuple[float, float, float] | None = None
    blended: Blend | None = None
    pick: str = ""
    note: str = ""
    error: str | None = None
    explanation: str | None = None
    gaps: tuple[str, ...] = field(default_factory=tuple)


def model_ready() -> bool:
    return MODEL_PATH.exists() and HISTORY_CSV.exists()


def clear_cache() -> None:
    _STORE.clear()


def _featured() -> pd.DataFrame:
    if "featured" not in _STORE:
        raw = load_history(download=False)
        cleaned = clean_history(raw)
        featured = build_features(cleaned)
        _STORE["featured"] = featured
        _STORE["snaps"] = team_snapshots(featured)
    return _STORE["featured"]


def _snaps() -> dict:
    _featured()
    return _STORE["snaps"]


def poly_from_ticket(
    fixture: str,
    favorite_team: str,
    favorite_yes: float | None,
    dog_yes: float | None,
) -> tuple[float, float] | None:
    sides = split_fixture(fixture)
    if sides is None or favorite_yes is None or dog_yes is None:
        return None
    home_name, away_name = sides
    fav_home = _closer(favorite_team, home_name, away_name)
    if fav_home:
        return float(favorite_yes), float(dog_yes)
    return float(dog_yes), float(favorite_yes)


def forecast_match(
    fixture: str,
    *,
    favorite_team: str = "",
    favorite_yes: float | None = None,
    dog_yes: float | None = None,
    kickoff: datetime | None = None,
    explain: bool = False,
) -> Forecast:
    sides = split_fixture(fixture)
    if sides is None:
        return Forecast("", "", error="Could not parse home / away from the fixture title.")
    home_name, away_name = sides
    if not model_ready():
        return Forecast(
            home_name,
            away_name,
            error="Train the 1X2 engine first: python -m pm_football_bot.predict.train",
        )

    try:
        featured = _featured()
        snaps = _snaps()
        bundle = load_ensemble()
    except Exception as exc:
        return Forecast(home_name, away_name, error=f"Could not load the 1X2 engine: {exc}")

    keys = list(snaps)
    matched_home = best_key(home_name, keys)
    matched_away = best_key(away_name, keys)
    if matched_home is None or matched_away is None:
        return Forecast(
            home_name,
            away_name,
            matched_home=matched_home,
            matched_away=matched_away,
            error=f"No history match for {home_name if matched_home is None else away_name}.",
        )

    kick = pd.Timestamp(kickoff) if kickoff is not None else pd.Timestamp.now(tz="UTC")
    row = inference_row(featured, matched_home, matched_away, kick, snaps)
    ml = predict_proba(bundle, row)

    poly = None
    sides_yes = poly_from_ticket(fixture, favorite_team, favorite_yes, dog_yes)
    if sides_yes is not None:
        poly = polymarket_1x2(*sides_yes)

    blended = blend_probabilities(ml, poly, None)
    labels = ("Home", "Draw", "Away")
    pick = labels[max(range(3), key=lambda i: (blended.home, blended.draw, blended.away)[i])]
    gaps = _gap_notes(ml, poly)
    note = (
        "Second view on a KEEP ticket — not a bet. "
        "ML uses rolling form / ELO / shot xG proxy only; live books are not in the model."
    )
    forecast = Forecast(
        home_name,
        away_name,
        matched_home=matched_home,
        matched_away=matched_away,
        ml=ml,
        poly=poly,
        blended=blended,
        pick=pick,
        note=note,
        gaps=gaps,
    )
    if explain:
        from pm_football_bot.predict.explain import explain_forecast

        return replace(forecast, explanation=explain_forecast(forecast))
    return forecast


def _closer(favorite: str, home: str, away: str) -> bool:
    fav = fold_name(favorite)
    h = fold_name(home)
    a = fold_name(away)
    if fav == h or fav in h:
        return True
    if fav == a or fav in a:
        return False
    return SequenceMatcher(None, fav, h).ratio() >= SequenceMatcher(None, fav, a).ratio()


def _gap_notes(
    ml: tuple[float, float, float],
    poly: tuple[float, float, float] | None,
) -> tuple[str, ...]:
    if poly is None:
        return ()
    notes: list[str] = []
    names = ("home", "draw", "away")
    for i, name in enumerate(names):
        delta = ml[i] - poly[i]
        if abs(delta) >= 0.10:
            lean = "ML" if delta > 0 else "Polymarket"
            notes.append(f"{name} {abs(delta):.0%} apart — {lean} is higher")
    return tuple(notes)
