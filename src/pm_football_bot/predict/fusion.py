from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Blend:
    home: float
    draw: float
    away: float
    sources: tuple[str, ...]
    weights: dict[str, float]


def _norm3(h: float, d: float, a: float) -> tuple[float, float, float]:
    total = h + d + a
    if total <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return h / total, d / total, a / total


def polymarket_1x2(home_yes: float, away_yes: float) -> tuple[float, float, float]:
    home = max(0.0, min(1.0, home_yes))
    away = max(0.0, min(1.0, away_yes))
    draw = max(0.0, 1.0 - home - away)
    return _norm3(home, draw, away)


def blend_probabilities(
    ml: tuple[float, float, float] | None,
    poly: tuple[float, float, float] | None,
    book: tuple[float, float, float] | None = None,
    *,
    w_ml: float = 0.45,
    w_poly: float = 0.35,
    w_book: float = 0.20,
    w_ml_no_book: float = 0.55,
    w_poly_no_book: float = 0.45,
) -> Blend:
    parts: list[tuple[str, tuple[float, float, float], float]] = []
    if ml is not None:
        parts.append(("ml", ml, w_ml if book is not None else w_ml_no_book))
    if poly is not None:
        parts.append(("poly", poly, w_poly if book is not None else w_poly_no_book))
    if book is not None:
        parts.append(("book", book, w_book if ml is not None and poly is not None else (0.40 if ml else 1.0)))

    if not parts:
        return Blend(1 / 3, 1 / 3, 1 / 3, (), {})

    if len(parts) == 1:
        h, d, a = _norm3(*parts[0][1])
        return Blend(h, d, a, (parts[0][0],), {parts[0][0]: 1.0})

    if book is None and ml is not None and poly is not None:
        weights = {"ml": w_ml_no_book, "poly": w_poly_no_book}
    elif ml is not None and poly is not None and book is not None:
        weights = {"ml": w_ml, "poly": w_poly, "book": w_book}
    else:
        raw = {name: w for name, _, w in parts}
        total_w = sum(raw.values())
        weights = {k: v / total_w for k, v in raw.items()}

    home = draw = away = 0.0
    for name, probs, _ in parts:
        w = weights[name]
        home += w * probs[0]
        draw += w * probs[1]
        away += w * probs[2]
    home, draw, away = _norm3(home, draw, away)
    return Blend(home, draw, away, tuple(weights), weights)


def entropy(h: float, d: float, a: float) -> float:
    vals = np.array([h, d, a], dtype=float)
    vals = vals[vals > 0]
    return float(-(vals * np.log(vals)).sum())
