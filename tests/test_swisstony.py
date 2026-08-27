from pm_football_bot.config import load_settings
from pm_football_bot.models import Ticket
from pm_football_bot.swisstony import league_of, matches_harvest, parent_slug, SwissLot


def _lot(**kwargs) -> SwissLot:
    base = dict(
        league="epl",
        parent="epl-ars-cov-2026-08-21",
        event_slug="epl-ars-cov-2026-08-21",
        title="Will Coventry City FC win on 2026-08-21?",
        outcome="No",
        shares_bought=373,
        avg_price=0.95,
        cost_usd=354,
        shares_now=373,
        mark_usd=350,
        u_pnl=-4,
        cur_price=0.95,
    )
    base.update(kwargs)
    return SwissLot(**base)


def test_parent_slug_strips_side_books():
    assert parent_slug("epl-ars-cov-2026-08-21-more-markets") == "epl-ars-cov-2026-08-21"
    assert parent_slug("lal-mad-mala-2026-08-19-exact-score") == "lal-mad-mala-2026-08-19"
    assert parent_slug("fl1-psg-ren-2026-08-23-more-markets") == "fl1-psg-ren-2026-08-23"
    assert parent_slug("sea-fro-juv-2026-08-23-more-markets") == "sea-fro-juv-2026-08-23"
    assert parent_slug("bun-bay-stu-2026-08-28-more-markets") == "bun-bay-stu-2026-08-28"
    assert league_of("ucl-fen-lyo-2026-08-18-more-markets") == "ucl"
    assert league_of("uel-mta-she-2026-07-30") == "uel"
    assert league_of("dfb-heb-bvb-2026-09-01") == "dfb"
    assert league_of("elc-wre-bir-2026-08-28") == "elc"
    assert league_of("efl-mun-grm-2026-09-16") == "efl"
    assert league_of("fl1-psg-ren-2026-08-23") == "ligue1"
    assert league_of("sea-fro-juv-2026-08-23") == "seriea"
    assert league_of("bun-bay-stu-2026-08-28") == "bundesliga"
    assert league_of("mls-chi-van-2026-07-16") is None


def test_five_euro_leagues_enabled():
    enabled = {lg.key: lg for lg in load_settings().leagues if lg.enabled}
    assert set(enabled) == {"epl", "laliga", "ligue1", "seriea", "bundesliga"}
    board = {lg.key for lg in load_settings().leagues}
    assert {"ucl", "uel", "efl", "elc", "efa", "dfb", "itc", "cdr"}.issubset(board)
    by_key = {lg.key: lg for lg in load_settings().leagues}
    assert by_key["efl"].series_id == "10329"
    assert by_key["efa"].series_id == "10314"
    assert enabled["ligue1"].series_id == "10195"
    assert enabled["seriea"].series_id == "10203"
    assert enabled["bundesliga"].series_id == "10194"


def test_matches_game_totals_not_team_totals():
    ticket = Ticket(
        league="epl",
        fixture="Arsenal FC vs. Coventry City FC",
        slug="epl-ars-cov-2026-08-21",
        rule_id="over_0_5",
        question="O/U 0.5",
        token_id="1",
        outcome="Over",
        price=0.96,
        shares=19,
        cost_usd=18,
        spread=0.01,
        reason="x",
        dog_team="Coventry City FC",
    )
    game = _lot(
        title="Arsenal FC vs. Coventry City FC: O/U 0.5",
        outcome="Over",
        event_slug="epl-ars-cov-2026-08-21-more-markets",
    )
    team = _lot(
        title="Arsenal FC vs. Coventry City FC: Coventry City FC O/U 2.5",
        outcome="Under",
    )
    assert matches_harvest(ticket, game)
    assert not matches_harvest(ticket, team)
