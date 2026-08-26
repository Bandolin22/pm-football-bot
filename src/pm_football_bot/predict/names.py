from __future__ import annotations

from pm_football_bot.scout import _name_score, fold_name

# football-data.co.uk short names → same fold space as Polymarket titles.
_EXTRA = {
    "man united": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham",
    "tottenham": "tottenham",
    "nott'm forest": "nottingham forest",
    "nottm forest": "nottingham forest",
    "wolves": "wolverhampton wanderers",
    "ath madrid": "atletico madrid",
    "atletico": "atletico madrid",
    "ath bilbao": "athletic",
    "ac milan": "milan",
    "inter": "internazionale milano",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "fc koln": "koln",
    "koln": "koln",
    "bayern munich": "bayern munchen",
    "ein frankfurt": "eintracht frankfurt",
    "m'gladbach": "monchengladbach",
    "gladbach": "monchengladbach",
    "verona": "hellas verona",
    "betis": "real betis",
    "sociedad": "real sociedad",
}


def canon_name(name: str) -> str:
    folded = fold_name(name)
    return _EXTRA.get(folded, folded)


def best_key(query: str, keys: list[str]) -> str | None:
    target = canon_name(query)
    if not target or not keys:
        return None
    ranked = [(_name_score(target, canon_name(key)), key) for key in keys]
    ranked.sort(key=lambda item: item[0], reverse=True)
    if ranked and ranked[0][0] >= 0.72:
        return ranked[0][1]
    return None
