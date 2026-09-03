from pm_football_bot.account import (
    AccountLot,
    _enrich_teams,
    apply_taker_fees,
    classify_factor,
    is_soccer,
    parse_teams,
    summarize_factors,
    summarize_teams,
    taker_fee_usdc,
    team_identity,
    totals,
    watch_clubs_for,
)
from pm_football_bot.board import matched_watch_club, watch_display_name
from pm_football_bot.scout import fold_name


def _lot(
    title: str,
    outcome: str,
    pnl: float,
    *,
    slug: str = "sea-lec-rom-2026-08-31-more-markets",
    status: str = "closed",
    avg: float = 0.91,
    cost: float = 4.55,
    fee: float = 0.0,
) -> AccountLot:
    teams = parse_teams(title)
    return AccountLot(
        title=title,
        outcome=outcome,
        event_slug=slug,
        avg_price=avg,
        shares=5,
        cost_usd=cost,
        mark_usd=0.0 if status == "closed" else 4.6,
        pnl=pnl,
        cur_price=None if status == "closed" else 0.92,
        end_date="2026-08-31",
        status=status,
        factor=classify_factor(title, outcome, avg),
        teams=teams,
        watch_clubs=watch_clubs_for(teams),
        soccer=is_soccer(title, slug),
        fee_usd=fee,
    )


def test_classify_harvest_factors():
    assert classify_factor("Will Málaga CF win on 2026-08-19?", "No", 0.91) == "dog_no"
    assert classify_factor("Will Málaga CF win on 2026-08-19?", "No", 0.40) == "other"
    assert classify_factor("Will Club Atlético de Madrid win on 2026-08-19?", "Yes", 0.73) == "other"
    assert classify_factor("US Lecce vs. AS Roma: O/U 0.5", "Over") == "over_05"
    assert classify_factor("US Lecce vs. AS Roma: O/U 5.5", "Under") == "under_55"
    assert classify_factor("US Lecce vs. AS Roma: O/U 4.5", "Under") == "other"
    assert classify_factor("Exact Score: Any Other Score?", "No") == "exact_no"
    assert classify_factor("Exact Score: Lecce 1-0 Roma?", "Yes") == "other"
    assert classify_factor("US Lecce vs. AS Roma: O/U 8.5 corners", "Over") == "corner"
    assert (
        classify_factor(
            "Will Hamburg-Eimsbütteler BC vs. BV Borussia 09 Dortmund end in a draw?",
            "No",
            0.988,
        )
        == "other"
    )


def test_parse_teams_from_titles():
    assert parse_teams("Will VfL Osnabrück win on 2026-09-02?") == ("VfL Osnabrück",)
    assert parse_teams("US Lecce vs. AS Roma: O/U 5.5") == ("US Lecce", "AS Roma")
    home, away = parse_teams(
        "Will Hamburg-Eimsbütteler BC vs. BV Borussia 09 Dortmund end in a draw?"
    )
    assert "Hamburg" in home and "Dortmund" in away
    assert parse_teams("Exact Score: Any Other Score?") == ()


def test_watch_club_aliases():
    assert matched_watch_club("AS Roma") == "AS Roma"
    assert matched_watch_club("SSC Napoli") == "SSC Napoli"
    assert matched_watch_club("Club Atlético de Madrid") == "Atletic Madrid"
    assert matched_watch_club("Sporting CP") == "Sporting CP"
    assert matched_watch_club("Sporting Lisbon") == "Sporting CP"
    assert matched_watch_club("FC Porto") == "FC Porto"
    assert matched_watch_club("Porto") == "FC Porto"
    assert matched_watch_club("SL Benfica") == "SL Benfica"
    assert matched_watch_club("Sport Lisboa e Benfica") == "SL Benfica"
    assert matched_watch_club("Sporting Kansas City") is None
    assert matched_watch_club("Sporting Gijon") is None
    assert watch_clubs_for(("US Lecce", "AS Roma")) == ("AS Roma",)
    assert watch_clubs_for(("Sporting CP", "CD Nacional")) == ("Sporting CP",)
    assert watch_display_name("AS Roma") == "Roma"
    assert watch_display_name("Sporting CP") == "Sporting"
    assert watch_display_name("FC Porto") == "Porto"
    assert watch_display_name("SL Benfica") == "Benfica"
    assert watch_display_name("man united") == "Manchester United"


def test_soccer_filter_skips_crypto():
    assert is_soccer("US Lecce vs. AS Roma: O/U 0.5", "sea-lec-rom-2026-08-31-more-markets")
    assert not is_soccer(
        "Bitcoin Up or Down - June 9, 1:30PM-1:45PM ET",
        "btc-updown-15m-1781026200",
    )


def test_factor_and_team_pnl():
    lots = [
        _lot("Will US Lecce win on 2026-08-31?", "No", 1.10, slug="sea-lec-rom-2026-08-31"),
        _lot("US Lecce vs. AS Roma: O/U 0.5", "Over", 0.40),
        _lot("US Lecce vs. AS Roma: O/U 5.5", "Under", -0.20),
        _lot("Exact Score: Any Other Score?", "No", 0.15, slug="sea-lec-rom-2026-08-31-exact-score"),
        _lot("US Lecce vs. AS Roma: O/U 8.5 corners", "Over", -0.08),
        _lot(
            "Bitcoin Up or Down - June 9",
            "Down",
            16.2,
            slug="btc-updown-15m-1781026200",
        ),
    ]
    _enrich_teams(lots)
    factors = {row.key: row for row in summarize_factors(lots, soccer_only=True)}
    assert factors["dog_no"].lots == 1 and factors["dog_no"].pnl == 1.10
    assert factors["over_05"].pnl == 0.40
    assert factors["under_55"].pnl == -0.20
    assert factors["exact_no"].pnl == 0.15
    assert factors["corner"].pnl == -0.08
    assert factors["other"].lots == 0
    teams = {row.key: row for row in summarize_teams(lots, soccer_only=True, watchlist_only=True)}
    assert "AS Roma" in teams
    assert teams["AS Roma"].lots == 5
    # Crypto is excluded from soccer factor totals.
    assert sum(row.pnl for row in factors.values()) == 1.37
    all_teams = {row.key: row for row in summarize_teams(lots, soccer_only=True, watchlist_only=False)}
    lecce_key = fold_name("US Lecce")
    assert lecce_key in all_teams
    assert all_teams[lecce_key].lots == 5
    assert "AS Roma" in all_teams


def test_weekday_side_is_not_dropped():
    lots = [
        _lot("Will Hull City AFC win on 2026-08-22?", "No", -9.14, slug="elc-hul-mun-2026-08-22"),
        _lot(
            "Manchester United FC vs. Ipswich Town FC: O/U 5.5",
            "Under",
            -5.52,
            slug="elc-hul-mun-2026-08-22-more-markets",
        ),
    ]
    _enrich_teams(lots)
    all_teams = summarize_teams(lots, soccer_only=True, watchlist_only=False)
    labels = {row.label for row in all_teams}
    keys = {row.key for row in all_teams}
    assert "Hull City AFC" in labels or fold_name("Hull City AFC") in keys
    assert any("united" in row.label.lower() or row.key == "man united" for row in all_teams)


def test_taker_fee_formula_and_net_pnl():
    assert taker_fee_usdc(5, 0.92) == 0.0184
    assert taker_fee_usdc(5, 0.96) == 0.0096
    lot = _lot("US Lecce vs. AS Roma: O/U 0.5", "Over", 0.40, avg=0.92, fee=0.0184)
    assert lot.net_pnl == round(0.40 - 0.0184, 4)
    summary = totals([lot], soccer_only=True)
    assert summary["fees"] == 0.0184
    assert summary["gross_pnl"] == 0.40
    assert summary["pnl"] == round(0.40 - 0.0184, 4)


def test_apply_activity_fees_beats_estimate():
    lot = _lot("Torino FC vs. AC Monza: O/U 0.5", "Over", 0.0, avg=0.92, slug="itc-tor-mon1-2026-09-01-more-markets")
    apply_taker_fees(
        [lot],
        [
            {
                "type": "TRADE",
                "side": "BUY",
                "size": 5,
                "price": 0.92,
                "usdcSize": 4.6184,
                "title": lot.title,
                "outcome": "Over",
            }
        ],
    )
    assert lot.fee_usd == 0.0184


def test_team_identity_keeps_weekday_names():
    key, label = team_identity("US Lecce")
    assert key == fold_name("US Lecce")
    assert "Lecce" in label
    watch_key, watch_label = team_identity("AS Roma")
    assert watch_key == "AS Roma"
    assert watch_label == "Roma"
