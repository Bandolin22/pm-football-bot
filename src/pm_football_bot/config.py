from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def _as_side(value: Any) -> str | None:
    if value is None:
        return None
    if value is False:
        return "no"
    if value is True:
        return "yes"
    return str(value)


@dataclass(frozen=True)
class League:
    key: str
    name: str
    sport: str
    series_id: str
    primary_tag_id: int
    enabled: bool = True


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    enabled: bool = True
    side: str | None = None
    line: float | None = None


@dataclass(frozen=True)
class Settings:
    dry_run: bool
    poll_seconds: int
    gamma_host: str
    clob_host: str
    bankroll_usd: float
    ticket_usd: float
    min_shares: float
    max_tickets_per_fixture: int
    max_open_usd: float
    max_days_to_kickoff: int
    min_hours_to_kickoff: int
    leagues: tuple[League, ...]
    rules: tuple[Rule, ...]
    max_dog_yes: float
    min_favorite_yes: float
    price_min: float
    price_max: float
    max_spread: float
    min_best_bid: float


def load_settings() -> Settings:
    raw = _load_yaml("settings.yaml")
    leagues_raw = _load_yaml("leagues.yaml")
    strategy = _load_yaml("strategy.yaml")

    leagues: list[League] = []
    for key, row in (leagues_raw.get("leagues") or {}).items():
        leagues.append(
            League(
                key=key,
                name=str(row["name"]),
                sport=str(row["sport"]),
                series_id=str(row["series_id"]),
                primary_tag_id=int(row["primary_tag_id"]),
                enabled=bool(row.get("enabled", True)),
            )
        )

    rules: list[Rule] = []
    for row in strategy.get("rules") or []:
        line = row.get("line")
        rules.append(
            Rule(
                id=str(row["id"]),
                kind=str(row["kind"]),
                enabled=bool(row.get("enabled", True)),
                side=_as_side(row.get("side")),
                line=float(line) if line is not None else None,
            )
        )

    mismatch = strategy.get("mismatch") or {}
    band = strategy.get("price_band") or {}
    liq = strategy.get("liquidity") or {}

    return Settings(
        dry_run=bool(raw["dry_run"]),
        poll_seconds=int(raw["poll_seconds"]),
        gamma_host=str(raw["gamma_host"]).rstrip("/"),
        clob_host=str(raw["clob_host"]).rstrip("/"),
        bankroll_usd=float(raw["bankroll_usd"]),
        ticket_usd=float(raw["ticket_usd"]),
        min_shares=float(raw["min_shares"]),
        max_tickets_per_fixture=int(raw["max_tickets_per_fixture"]),
        max_open_usd=float(raw["max_open_usd"]),
        max_days_to_kickoff=int(raw["max_days_to_kickoff"]),
        min_hours_to_kickoff=int(raw["min_hours_to_kickoff"]),
        leagues=tuple(leagues),
        rules=tuple(rules),
        max_dog_yes=float(mismatch["max_dog_yes"]),
        min_favorite_yes=float(mismatch["min_favorite_yes"]),
        price_min=float(band["min"]),
        price_max=float(band["max"]),
        max_spread=float(liq["max_spread"]),
        min_best_bid=float(liq["min_best_bid"]),
    )
