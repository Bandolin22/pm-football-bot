from pm_football_bot.simulate import infer_teams, parse_lot, simulate_fixture
from pm_football_bot.swisstony import SwissLot


def _lot(title: str, outcome: str, shares: float, price: float) -> SwissLot:
    return SwissLot(
        league="epl",
        parent="epl-ars-cov-2026-08-21",
        event_slug="epl-ars-cov-2026-08-21",
        title=title,
        outcome=outcome,
        shares_bought=shares,
        avg_price=price,
        cost_usd=round(shares * price, 2),
        shares_now=shares,
        mark_usd=round(shares * price, 2),
        u_pnl=0,
        cur_price=price,
    )


def _book():
    return [
        _lot("Will Coventry City FC win on 2026-08-21?", "No", 100, 0.95),
        _lot("Arsenal FC vs. Coventry City FC: O/U 0.5", "Over", 50, 0.96),
        _lot("Arsenal FC vs. Coventry City FC: O/U 5.5", "Under", 40, 0.90),
        _lot("Arsenal FC vs. Coventry City FC: Coventry City FC O/U 2.5", "Under", 200, 0.98),
        _lot("Spread: Málaga CF (-2.5)", "No", 80, 0.99),  # wrong fixture names on purpose? skip
    ]


def test_infer_teams():
    lots = [
        _lot("Arsenal FC vs. Coventry City FC: O/U 0.5", "Over", 10, 0.9),
        _lot("Will Coventry City FC win on 2026-08-21?", "No", 10, 0.9),
    ]
    assert infer_teams(lots) == ("Arsenal FC", "Coventry City FC")


def test_harvest_2_0_is_near_best():
    lots = [
        _lot("Will Coventry City FC win on 2026-08-21?", "No", 100, 0.95),
        _lot("Arsenal FC vs. Coventry City FC: O/U 0.5", "Over", 50, 0.96),
        _lot("Arsenal FC vs. Coventry City FC: O/U 5.5", "Under", 40, 0.90),
        _lot("Arsenal FC vs. Coventry City FC: Coventry City FC O/U 2.5", "Under", 200, 0.98),
    ]
    sim = simulate_fixture(lots)
    assert sim is not None
    typical = sim.at(2, 0)
    blowout_goals = sim.at(6, 1)
    upset = sim.at(0, 1)
    nil = sim.at(0, 0)
    assert typical is not None and upset is not None and nil is not None and blowout_goals is not None
    assert typical.profit > 0
    assert upset.profit < typical.profit
    assert nil.profit < typical.profit
    assert blowout_goals.profit < typical.profit


def test_dog_win_kills_fade():
    lot = _lot("Will Coventry City FC win on 2026-08-21?", "No", 100, 0.95)
    parsed = parse_lot(lot, "Arsenal FC", "Coventry City FC")
    assert parsed.wins_on("Arsenal FC", "Coventry City FC", 2, 0) is True
    assert parsed.wins_on("Arsenal FC", "Coventry City FC", 0, 1) is False
    assert parsed.wins_on("Arsenal FC", "Coventry City FC", 0, 0) is True


def test_malaga_spread_no_is_almost_sure():
    lot = _lot("Spread: Málaga CF (-2.5)", "No", 100, 0.99)
    parsed = parse_lot(lot, "Club Atlético de Madrid", "Málaga CF")
    assert parsed.modeled
    assert parsed.wins_on("Club Atlético de Madrid", "Málaga CF", 2, 0) is True
    assert parsed.wins_on("Club Atlético de Madrid", "Málaga CF", 0, 3) is False
