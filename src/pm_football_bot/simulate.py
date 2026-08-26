from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from pm_football_bot.swisstony import SwissLot, lots_for_parent

MAX_GOALS = 7

_VS = re.compile(r"^(?P<home>.+?)\s+vs\.?\s+(?P<away>.+?)(?:\s*[-:]|\s*$)", re.I)
_WIN = re.compile(r"^will\s+(.+?)\s+win on\s+", re.I)
_DRAW = re.compile(r"end in a draw", re.I)
_SPREAD = re.compile(r"^spread:\s*(.+?)\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*\)", re.I)
_EXACT = re.compile(
    r"exact score:\s*(.+?)\s+(\d+)\s*[-–]\s*(\d+)\s+(.+?)\??\s*$",
    re.I,
)
_GAME_OU = re.compile(r":\s*(?:1st half |2nd half )?o/u\s*(\d+(?:\.\d+)?)\s*$", re.I)
_TEAM_OU = re.compile(r":\s*(.+?)\s+(?:1st half |2nd half )?o/u\s*(\d+(?:\.\d+)?)\s*$", re.I)


def _norm(name: str) -> str:
    text = f" {name.casefold()} "
    for bit in (
        " fc ",
        " cf ",
        " afc ",
        " sc ",
        " club ",
        " de ",
        " the ",
        " ud ",
        " cd ",
        " rc ",
        " rcd ",
        " ca ",
        " sk ",
    ):
        text = text.replace(bit, " ")
    return " ".join(text.split())


def same_team(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and a in b:
        return True
    if len(b) >= 5 and b in a:
        return True
    return False


def open_shares(lot: SwissLot) -> float:
    return lot.shares_now if lot.shares_now > 0 else lot.shares_bought


def open_cost(lot: SwissLot) -> float:
    return round(lot.avg_price * open_shares(lot), 4)


def infer_teams(lots: list[SwissLot]) -> tuple[str, str] | None:
    for lot in lots:
        title = lot.title.strip()
        if "first team to score" in title.lower():
            title = re.split(r"\s+-\s+first team", title, flags=re.I)[0]
        match = _VS.match(title)
        if not match:
            continue
        home, away = match.group("home").strip(), match.group("away").strip()
        away = re.split(r"\s+-\s+", away, maxsplit=1)[0].strip()
        if home and away and "win on" not in home.lower():
            return home, away
    return None


def _held_yes(outcome: str, team: str | None = None) -> bool | None:
    label = outcome.strip().casefold()
    if label in {"yes", "over"}:
        return True
    if label in {"no", "under"}:
        return False
    if team and same_team(outcome, team):
        return True
    return None


@dataclass(frozen=True)
class ParsedLot:
    lot: SwissLot
    modeled: bool
    kind: str
    detail: str

    def wins_on(self, home: str, away: str, hg: int, ag: int) -> bool | None:
        if not self.modeled:
            return None
        return _wins(self, home, away, hg, ag)


def parse_lot(lot: SwissLot, home: str, away: str) -> ParsedLot:
    title = lot.title.strip()
    low = title.lower()
    outcome = lot.outcome

    if any(
        bit in low
        for bit in (
            "1st half",
            "2nd half",
            "first half",
            "second half",
            "first team to score",
            "anytime goalscorer",
        )
    ):
        return ParsedLot(lot, False, "half", "Needs half-time / first scorer, not just FT")

    exact = _EXACT.search(title)
    if exact or "exact score" in low:
        if not exact:
            return ParsedLot(lot, False, "exact", "Could not parse exact score")
        h_name, hs, aws, a_name = exact.group(1), int(exact.group(2)), int(exact.group(3)), exact.group(4)
        want_h, want_a = hs, aws
        if same_team(h_name, away) and same_team(a_name, home):
            want_h, want_a = aws, hs
        held = _held_yes(outcome)
        if held is None:
            return ParsedLot(lot, False, "exact", f"Unknown exact outcome {outcome}")
        return ParsedLot(lot, True, "exact", f"{want_h}-{want_a}|{int(held)}")

    if _DRAW.search(title):
        held = _held_yes(outcome)
        if held is None:
            return ParsedLot(lot, False, "draw", f"Unknown draw outcome {outcome}")
        return ParsedLot(lot, True, "draw", str(int(held)))

    win = _WIN.match(title)
    if win:
        team = win.group(1).strip()
        held = _held_yes(outcome, team)
        if held is None:
            return ParsedLot(lot, False, "win", f"Unknown win outcome {outcome}")
        return ParsedLot(lot, True, "win", f"{team}|{int(held)}")

    spread = _SPREAD.match(title)
    if spread:
        team, line = spread.group(1).strip(), float(spread.group(2))
        held = _held_yes(outcome, team)
        if held is None:
            # other team name = fading this spread
            held = False if same_team(outcome, home) or same_team(outcome, away) else None
            if held is None:
                return ParsedLot(lot, False, "spread", f"Unknown spread outcome {outcome}")
            if same_team(outcome, team):
                held = True
        return ParsedLot(lot, True, "spread", f"{team}|{line}|{int(held)}")

    if "both teams to score" in low:
        held = _held_yes(outcome)
        if held is None:
            return ParsedLot(lot, False, "btts", f"Unknown BTTS outcome {outcome}")
        return ParsedLot(lot, True, "btts", str(int(held)))

    if "o/u" in low:
        tail = title.split(":")[-1].strip() if ":" in title else title
        tail_low = tail.lower()
        match = re.search(r"o/u\s*(\d+(?:\.\d+)?)", tail_low)
        if match:
            line = float(match.group(1))
            held = _held_yes(outcome)
            if held is None:
                return ParsedLot(lot, False, "ou", f"Unknown total outcome {outcome}")
            is_game = bool(re.match(r"^(1st half |2nd half )?o/u\b", tail_low))
            if is_game:
                return ParsedLot(lot, True, "game_ou", f"{line}|{int(held)}")
            team = re.sub(r"\s+o/u\s+\d+(?:\.\d+)?\s*$", "", tail, flags=re.I).strip()
            if team:
                return ParsedLot(lot, True, "team_ou", f"{team}|{line}|{int(held)}")

    return ParsedLot(lot, False, "other", "Unrecognized market")


def _goals_for(team: str, home: str, away: str, hg: int, ag: int) -> int | None:
    if same_team(team, home):
        return hg
    if same_team(team, away):
        return ag
    return None


def _wins(parsed: ParsedLot, home: str, away: str, hg: int, ag: int) -> bool:
    kind, detail = parsed.kind, parsed.detail
    if kind == "draw":
        is_draw = hg == ag
        return is_draw if detail == "1" else (not is_draw)
    if kind == "win":
        team, held = detail.rsplit("|", 1)
        g = _goals_for(team, home, away, hg, ag)
        opp = ag if g == hg else hg
        if g is None:
            return False
        won = g > opp
        return won if held == "1" else (not won)
    if kind == "game_ou":
        line, held = detail.split("|")
        over = (hg + ag) > float(line)
        return over if held == "1" else (not over)
    if kind == "team_ou":
        team, line, held = detail.split("|")
        g = _goals_for(team, home, away, hg, ag)
        if g is None:
            return False
        over = g > float(line)
        return over if held == "1" else (not over)
    if kind == "spread":
        team, line, held = detail.split("|")
        g = _goals_for(team, home, away, hg, ag)
        if g is None:
            return False
        opp = ag if same_team(team, home) else hg
        cover = (g + float(line)) > opp
        return cover if held == "1" else (not cover)
    if kind == "exact":
        hs_as, held = detail.split("|")
        want_h, want_a = (int(x) for x in hs_as.split("-"))
        hit = hg == want_h and ag == want_a
        return hit if held == "1" else (not hit)
    if kind == "btts":
        both = hg > 0 and ag > 0
        return both if detail == "1" else (not both)
    return False


@dataclass(frozen=True)
class ScorePnl:
    home_goals: int
    away_goals: int
    payout: float
    cost: float
    profit: float
    winners: int
    losers: int


@dataclass(frozen=True)
class FixtureSim:
    parent: str
    league: str
    home: str
    away: str
    title: str
    modeled: list[ParsedLot]
    unmodeled: list[ParsedLot]
    paths: tuple[ScorePnl, ...]
    unmodeled_cost: float
    unmodeled_best: float
    mark_usd: float
    open_cost: float

    @property
    def best(self) -> ScorePnl:
        return max(self.paths, key=lambda row: row.profit)

    @property
    def worst(self) -> ScorePnl:
        return min(self.paths, key=lambda row: row.profit)

    def at(self, hg: int, ag: int) -> ScorePnl | None:
        for row in self.paths:
            if row.home_goals == hg and row.away_goals == ag:
                return row
        return None


def _score_pnl(parsed: list[ParsedLot], home: str, away: str, hg: int, ag: int) -> ScorePnl:
    payout = 0.0
    cost = 0.0
    winners = 0
    losers = 0
    for item in parsed:
        shares = open_shares(item.lot)
        basis = open_cost(item.lot)
        cost += basis
        won = item.wins_on(home, away, hg, ag)
        if won:
            payout += shares
            winners += 1
        else:
            losers += 1
    return ScorePnl(
        home_goals=hg,
        away_goals=ag,
        payout=round(payout, 2),
        cost=round(cost, 2),
        profit=round(payout - cost, 2),
        winners=winners,
        losers=losers,
    )


def simulate_fixture(lots: list[SwissLot], parent: str | None = None) -> FixtureSim | None:
    if not lots:
        return None
    teams = infer_teams(lots)
    if teams is None:
        return None
    home, away = teams
    parsed = [parse_lot(lot, home, away) for lot in lots]
    modeled = [row for row in parsed if row.modeled]
    unmodeled = [row for row in parsed if not row.modeled]
    if not modeled:
        return None
    paths = tuple(
        _score_pnl(modeled, home, away, hg, ag)
        for hg in range(0, MAX_GOALS + 1)
        for ag in range(0, MAX_GOALS + 1)
    )
    u_cost = sum(open_cost(row.lot) for row in unmodeled)
    u_best = sum(open_shares(row.lot) - open_cost(row.lot) for row in unmodeled)
    title = next((lot.title.split(":")[0] for lot in lots if " vs" in lot.title.lower()), f"{home} vs {away}")
    return FixtureSim(
        parent=parent or lots[0].parent,
        league=lots[0].league,
        home=home,
        away=away,
        title=title,
        modeled=modeled,
        unmodeled=unmodeled,
        paths=paths,
        unmodeled_cost=round(u_cost, 2),
        unmodeled_best=round(u_best, 2),
        mark_usd=round(sum(lot.mark_usd for lot in lots), 2),
        open_cost=round(sum(open_cost(lot) for lot in lots), 2),
    )


KEY_SCORES = (
    (2, 0, "Home 2-0 (typical mismatch)"),
    (1, 0, "Home 1-0"),
    (0, 0, "0-0"),
    (1, 1, "1-1"),
    (0, 1, "Away 1-0 (dog wins if away is dog)"),
    (4, 0, "Home 4-0"),
    (3, 2, "3-2 (five goals)"),
    (6, 1, "6-1 (kills Under 5.5)"),
)


@dataclass(frozen=True)
class BookSim:
    fixtures: tuple[FixtureSim, ...]
    skipped: int

    @property
    def modeled_best(self) -> float:
        return round(sum(item.best.profit for item in self.fixtures), 2)

    @property
    def modeled_worst(self) -> float:
        return round(sum(item.worst.profit for item in self.fixtures), 2)

    @property
    def conservative_worst(self) -> float:
        extra = sum(-item.unmodeled_cost for item in self.fixtures)
        return round(self.modeled_worst + extra, 2)

    @property
    def optimistic_best(self) -> float:
        extra = sum(item.unmodeled_best for item in self.fixtures)
        return round(self.modeled_best + extra, 2)

    @property
    def mark_usd(self) -> float:
        return round(sum(item.mark_usd for item in self.fixtures), 2)

    @property
    def open_cost(self) -> float:
        return round(sum(item.open_cost for item in self.fixtures), 2)

    @property
    def modeled_cost(self) -> float:
        return round(sum(item.best.cost for item in self.fixtures), 2)


def simulate_book(lots: list[SwissLot]) -> BookSim:
    groups: dict[str, list[SwissLot]] = defaultdict(list)
    for lot in lots:
        groups[lot.parent].append(lot)
    fixtures: list[FixtureSim] = []
    skipped = 0
    for parent, group in groups.items():
        sim = simulate_fixture(group, parent)
        if sim is None:
            skipped += 1
            continue
        fixtures.append(sim)
    fixtures.sort(key=lambda row: row.best.cost, reverse=True)
    return BookSim(fixtures=tuple(fixtures), skipped=skipped)


def lot_results(sim: FixtureSim, hg: int, ag: int) -> list[tuple[ParsedLot, bool, float]]:
    rows = []
    for item in sim.modeled:
        won = bool(item.wins_on(sim.home, sim.away, hg, ag))
        shares = open_shares(item.lot)
        profit = (shares if won else 0.0) - open_cost(item.lot)
        rows.append((item, won, round(profit, 2)))
    rows.sort(key=lambda row: row[2])
    return rows


def scoreline(sim: FixtureSim, hg: int, ag: int) -> str:
    return f"{sim.home} {hg}–{ag} {sim.away}"


def lots_by_parent(lots: list[SwissLot], parent: str) -> list[SwissLot]:
    return lots_for_parent(lots, parent)
