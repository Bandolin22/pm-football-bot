from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pm_football_bot.gamma import invert_book
from pm_football_bot.keeper import (
    KeeperConfig,
    PlacedOrder,
    classify_clob_status,
    dog_never_beat,
    is_any_other_score,
    is_exact_score_00,
    is_neither_first_to_score,
    is_over_goals,
    load_keeper_config,
    load_placed,
    load_placed_book,
    open_tickets,
    order_id_from_result,
    place_keeper_book,
    propose_keeper_tickets,
    refresh_placed_orders,
    save_placed,
    save_placed_book,
    ticket_key,
)
from pm_football_bot.models import BinaryMarket, Fixture, OutcomeBook, SideQuote
from pm_football_bot.scout import Briefing, TeamPulse


def _book(token: str, label: str, mid: float, bid: float, ask: float) -> OutcomeBook:
    return OutcomeBook(token_id=token, label=label, mid=mid, best_bid=bid, best_ask=ask)


def _side(team: str, yes_mid: float) -> SideQuote:
    yes_bid, yes_ask = round(yes_mid - 0.004, 4), round(yes_mid + 0.004, 4)
    no_bid, no_ask = invert_book(yes_bid, yes_ask)
    return SideQuote(
        team=team,
        yes=_book(f"{team}-yes", "Yes", yes_mid, yes_bid, yes_ask),
        no=_book(f"{team}-no", "No", round(1 - yes_mid, 4), no_bid, no_ask),
    )


def _binary(
    question: str,
    kind: str,
    line: float | None,
    yes_mid: float,
    slug: str = "",
) -> BinaryMarket:
    yes_bid, yes_ask = round(yes_mid - 0.003, 4), round(yes_mid + 0.003, 4)
    no_bid, no_ask = invert_book(yes_bid, yes_ask)
    over_label = "Over" if kind in {"totals", "total", "corners"} or "corner" in question.lower() or "O/U" in question else "Yes"
    under_label = "Under" if over_label == "Over" else "No"
    if "Neither" in question or "0-0" in question or "Any Other" in question:
        over_label, under_label = "Yes", "No"
        yes_mid_use = yes_mid
        no_mid = round(1 - yes_mid, 4)
        return BinaryMarket(
            question=question,
            slug=slug or question,
            kind=kind,
            line=line,
            outcomes=(
                _book(f"{slug or question}-yes", "Yes", yes_mid_use, yes_bid, yes_ask),
                _book(f"{slug or question}-no", "No", no_mid, no_bid, no_ask),
            ),
        )
    return BinaryMarket(
        question=question,
        slug=slug or question,
        kind=kind,
        line=line,
        outcomes=(
            _book(f"{slug or question}-over", over_label, yes_mid, yes_bid, yes_ask),
            _book(f"{slug or question}-under", under_label, round(1 - yes_mid, 4), no_bid, no_ask),
        ),
    )


def _pulse(**kwargs) -> TeamPulse:
    base = dict(
        name="x",
        position=4,
        played=8,
        points=18,
        goal_diff=10,
        form="WWWDL",
        last_five=("W Arsenal 2–0 Coventry",),
        home_ppg=2.2,
        away_ppg=1.8,
        gf_pg=1.8,
        ga_pg=0.7,
        rest_days=6,
        next_match=None,
    )
    base.update(kwargs)
    return TeamPulse(**base)


def _cfg() -> KeeperConfig:
    return load_keeper_config()


def _fixture(
    *,
    home: str = "Arsenal FC",
    away: str = "Coventry City FC",
    home_yes: float = 0.88,
    away_yes: float = 0.05,
    extras: tuple[BinaryMarket, ...] = (),
    hours: float = 48,
    league: str = "epl",
) -> Fixture:
    kickoff = datetime.now(timezone.utc) + timedelta(hours=hours)
    return Fixture(
        league=league,
        title=f"{home} vs. {away}",
        slug="epl-ars-cov-2026-09-18",
        kickoff=kickoff,
        home=_side(home, home_yes),
        away=_side(away, away_yes),
        draw=None,
        extras=extras,
    )


def _briefing(
    home: str = "Arsenal FC",
    away: str = "Coventry City FC",
    home_pulse: TeamPulse | None = None,
    away_pulse: TeamPulse | None = None,
    h2h: tuple[str, ...] = ("Sep 2025 Arsenal 2–0 Coventry",),
    error: str | None = None,
) -> Briefing:
    return Briefing(
        home_name=home,
        away_name=away,
        home=home_pulse if home_pulse is not None else _pulse(name=home, form="WWWWL"),
        away=away_pulse if away_pulse is not None else _pulse(name=away, form="LLDLD", gf_pg=0.6),
        h2h=h2h,
        vetoes=(),
        error=error,
    )


def test_load_keeper_yaml_defaults():
    cfg = load_keeper_config()
    assert cfg.shares == 5
    assert cfg.home_favorite_min == 0.80
    assert cfg.away_favorite_min == 0.85
    assert cfg.hard_price == 0.98
    assert cfg.prolific_gf_pg == 2.2
    assert cfg.prolific_match_goals == 4.0


def test_home_watchlist_fade_requires_80():
    extras = (
        _binary("O/U 0.5 Goals", "totals", 0.5, 0.94),
        _binary("O/U 5.5 Goals", "totals", 5.5, 0.12),
    )
    strong = _briefing()
    tickets = propose_keeper_tickets(_fixture(home_yes=0.815, extras=extras), _cfg(), strong)
    assert "keeper_fade_dog" in [t.rule_id for t in tickets]
    fade = next(t for t in tickets if t.rule_id == "keeper_fade_dog")
    assert fade.outcome == "No"
    assert fade.shares == 5
    assert "Coventry" in fade.question

    weak = propose_keeper_tickets(_fixture(home_yes=0.79, extras=extras), _cfg(), strong)
    assert "keeper_fade_dog" not in [t.rule_id for t in weak]


def test_away_watchlist_fade_requires_85():
    extras = (_binary("O/U 0.5 Goals", "totals", 0.5, 0.94),)
    briefing = _briefing(home="Elche CF", away="Arsenal FC", home_pulse=_pulse(name="Elche CF", form="LLDLD"), away_pulse=_pulse(name="Arsenal FC", form="WWWWL"))
    ok = propose_keeper_tickets(
        _fixture(home="Elche CF", away="Arsenal FC", home_yes=0.08, away_yes=0.86, extras=extras),
        _cfg(),
        briefing,
    )
    assert "keeper_fade_dog" in [t.rule_id for t in ok]

    no = propose_keeper_tickets(
        _fixture(home="Elche CF", away="Arsenal FC", home_yes=0.10, away_yes=0.84, extras=extras),
        _cfg(),
        briefing,
    )
    assert "keeper_fade_dog" not in [t.rule_id for t in no]


def test_two_watchlist_skips_fade_under_and_corners():
    extras = (
        _binary("O/U 0.5 Goals", "totals", 0.5, 0.94),
        _binary("O/U 5.5 Goals", "totals", 5.5, 0.20),
        _binary("Total corners O/U 7.5", "totals", 7.5, 0.75, slug="corners-75"),
    )
    fixture = _fixture(home="Arsenal FC", away="Liverpool FC", home_yes=0.42, away_yes=0.38, extras=extras)
    briefing = _briefing(home="Arsenal FC", away="Liverpool FC")
    ids = [t.rule_id for t in propose_keeper_tickets(fixture, _cfg(), briefing)]
    assert "keeper_fade_dog" not in ids
    assert "keeper_under_5_5" not in ids
    assert "keeper_corners_7_5" not in ids
    assert "keeper_over_0_5" in ids


def test_over_picks_cheapest_equivalent():
    extras = (
        _binary("O/U 0.5 Goals", "totals", 0.5, 0.97),
        _binary("Neither team to score first?", "other", None, 0.10, slug="neither"),
        _binary("Exact Score 0-0", "other", None, 0.20, slug="zero"),
    )
    tickets = propose_keeper_tickets(_fixture(extras=extras), _cfg(), _briefing())
    over = next(t for t in tickets if t.rule_id == "keeper_over_0_5")
    assert over.outcome == "No"
    assert "0-0" in over.question
    assert over.price < 0.97


def test_over_falls_back_to_1_5_when_all_98():
    extras = (
        _binary("O/U 0.5 Goals", "totals", 0.5, 0.985),
        _binary("Neither team to score first?", "other", None, 0.015, slug="neither"),
        _binary("Exact Score 0-0", "other", None, 0.012, slug="zero"),
        _binary("O/U 1.5 Goals", "totals", 1.5, 0.90),
    )
    ids = [t.rule_id for t in propose_keeper_tickets(_fixture(extras=extras), _cfg(), _briefing())]
    assert "keeper_over_0_5" not in ids
    assert "keeper_over_1_5" in ids


def test_under_only_when_favorite_is_away():
    extras = (_binary("O/U 5.5 Goals", "totals", 5.5, 0.18),)
    home_fav = propose_keeper_tickets(_fixture(home_yes=0.88, away_yes=0.05, extras=extras), _cfg(), _briefing())
    assert "keeper_under_5_5" not in [t.rule_id for t in home_fav]

    briefing = _briefing(
        home="Elche CF",
        away="Arsenal FC",
        home_pulse=_pulse(name="Elche CF", form="LLDLD", gf_pg=0.7),
        away_pulse=_pulse(name="Arsenal FC", form="WWWWL", gf_pg=1.6),
    )
    away_fav = propose_keeper_tickets(
        _fixture(home="Elche CF", away="Arsenal FC", home_yes=0.07, away_yes=0.91, extras=extras),
        _cfg(),
        briefing,
    )
    assert "keeper_under_5_5" in [t.rule_id for t in away_fav]


def test_under_skips_recent_high_scoring_not_club_name():
    extras = (_binary("O/U 5.5 Goals", "totals", 5.5, 0.18),)
    hot = (
        "W Arsenal 4–1 Everton",
        "W Arsenal 3–2 Brentford",
        "W Arsenal 5–0 West Ham",
        "W Liverpool 2–3 Arsenal",
        "W Arsenal 4–2 Fulham",
    )
    briefing = _briefing(
        home="Elche CF",
        away="Arsenal FC",
        home_pulse=_pulse(name="Elche CF", form="LLDLD", gf_pg=0.7, last_five=("L Elche 0–1 Getafe",)),
        away_pulse=_pulse(name="Arsenal FC", form="WWWWL", gf_pg=1.5, last_five=hot),
    )
    hot_tickets = propose_keeper_tickets(
        _fixture(home="Elche CF", away="Arsenal FC", home_yes=0.07, away_yes=0.91, extras=extras),
        _cfg(),
        briefing,
    )
    assert "keeper_under_5_5" not in [t.rule_id for t in hot_tickets]

    quiet = (
        "W FC Barcelona 1–0 Elche",
        "W Getafe 0–1 FC Barcelona",
        "D FC Barcelona 1–1 Girona",
        "W FC Barcelona 2–0 Alaves",
        "L Sociedad 1–0 FC Barcelona",
    )
    barca = _briefing(
        home="Elche CF",
        away="FC Barcelona",
        home_pulse=_pulse(name="Elche CF", form="LLDLD", gf_pg=0.7, last_five=("L Elche 0–1 Getafe",)),
        away_pulse=_pulse(name="FC Barcelona", form="WWWWL", gf_pg=2.6, last_five=quiet),
    )
    quiet_tickets = propose_keeper_tickets(
        _fixture(home="Elche CF", away="FC Barcelona", home_yes=0.06, away_yes=0.91, extras=extras, league="laliga"),
        _cfg(),
        barca,
    )
    assert "keeper_under_5_5" in [t.rule_id for t in quiet_tickets]


def test_under_skips_barca_nickname_last_five():
    extras = (_binary("O/U 5.5 Goals", "totals", 5.5, 0.18),)
    hot = (
        "W Barça 7–2 Santander",
        "W Levante 2–4 Barça",
        "W Barça 5–1 Feyenoord",
        "W Valencia 0–5 Barça",
        "W Barça 5–2 Rayo Vallecano",
    )
    briefing = _briefing(
        home="Sevilla FC",
        away="FC Barcelona",
        home_pulse=_pulse(
            name="Sevilla FC",
            form="WWDLW",
            gf_pg=1.5,
            last_five=("W Deportivo 0–1 Sevilla FC", "W Sevilla FC 1–0 Valencia"),
        ),
        away_pulse=_pulse(name="FC Barcelona", form="WWWWW", gf_pg=1.5, last_five=hot),
    )
    tickets = propose_keeper_tickets(
        _fixture(
            home="Sevilla FC",
            away="FC Barcelona",
            home_yes=0.12,
            away_yes=0.82,
            extras=extras,
            league="laliga",
        ),
        _cfg(),
        briefing,
    )
    assert "keeper_under_5_5" not in [t.rule_id for t in tickets]


def test_under_skips_structured_last_five_scores():
    extras = (_binary("O/U 5.5 Goals", "totals", 5.5, 0.18),)
    briefing = _briefing(
        home="Sevilla FC",
        away="FC Barcelona",
        home_pulse=_pulse(name="Sevilla FC", gf_pg=1.5, last_five=()),
        away_pulse=_pulse(
            name="FC Barcelona",
            gf_pg=1.0,
            last_five=(),
            last_five_scores=((7, 2), (4, 2), (5, 1), (5, 0), (5, 2)),
        ),
    )
    tickets = propose_keeper_tickets(
        _fixture(
            home="Sevilla FC",
            away="FC Barcelona",
            home_yes=0.12,
            away_yes=0.82,
            extras=extras,
            league="laliga",
        ),
        _cfg(),
        briefing,
    )
    assert "keeper_under_5_5" not in [t.rule_id for t in tickets]


def test_under_skips_when_briefing_missing():
    extras = (_binary("O/U 5.5 Goals", "totals", 5.5, 0.18),)
    missing = Briefing("Elche CF", "FC Barcelona", None, None, (), (), error="no token")
    tickets = propose_keeper_tickets(
        _fixture(home="Elche CF", away="FC Barcelona", home_yes=0.06, away_yes=0.91, extras=extras, league="laliga"),
        _cfg(),
        missing,
    )
    assert "keeper_under_5_5" not in [t.rule_id for t in tickets]


def test_under_98_uses_any_other_score_no():
    extras = (
        _binary("O/U 5.5 Goals", "totals", 5.5, 0.015),
        _binary("Exact Score: Any Other Score", "other", None, 0.12, slug="else"),
    )
    briefing = _briefing(
        home="Elche CF",
        away="Arsenal FC",
        home_pulse=_pulse(name="Elche CF", form="LLDLD", gf_pg=0.7),
        away_pulse=_pulse(name="Arsenal FC", form="WWWWL", gf_pg=1.5),
    )
    tickets = propose_keeper_tickets(
        _fixture(home="Elche CF", away="Arsenal FC", home_yes=0.07, away_yes=0.91, extras=extras),
        _cfg(),
        briefing,
    )
    ids = [t.rule_id for t in tickets]
    assert "keeper_under_5_5" not in ids
    else_no = next(t for t in tickets if t.rule_id == "keeper_exact_else_no")
    assert else_no.outcome == "No"


def test_fade_tiny_dog_without_form_still_buys():
    extras = (_binary("O/U 0.5 Goals", "totals", 0.5, 0.94),)
    missing = Briefing("Sporting CP", "FC Arouca", None, None, (), (), error="No football-data.org competition for por.")
    fixture = _fixture(
        home="Sporting CP",
        away="FC Arouca",
        home_yes=0.815,
        away_yes=0.055,
        extras=extras,
        league="por",
    )
    ids = [t.rule_id for t in propose_keeper_tickets(fixture, _cfg(), missing)]
    assert "keeper_fade_dog" in ids
    fade = next(t for t in propose_keeper_tickets(fixture, _cfg(), missing) if t.rule_id == "keeper_fade_dog")
    assert fade.outcome == "No"
    assert "Arouca" in fade.question


def test_fade_skips_without_form_when_dog_is_not_tiny():
    extras = (_binary("O/U 0.5 Goals", "totals", 0.5, 0.94),)
    missing = Briefing("Arsenal FC", "West Ham United FC", None, None, (), (), error="no token")
    fixture = _fixture(home="Arsenal FC", away="West Ham United FC", home_yes=0.82, away_yes=0.18, extras=extras)
    ids = [t.rule_id for t in propose_keeper_tickets(fixture, _cfg(), missing)]
    assert "keeper_fade_dog" not in ids
    assert "keeper_over_0_5" in ids


def test_fade_allows_h2h_when_form_is_mixed():
    extras = (_binary("O/U 0.5 Goals", "totals", 0.5, 0.94),)
    briefing = _briefing(
        home_pulse=_pulse(name="Arsenal FC", form="WLWLD"),
        away_pulse=_pulse(name="Coventry City FC", form="WWLDL"),
        h2h=("Sep 2025 Arsenal 2–0 Coventry", "Jan 2024 Arsenal 1–1 Coventry"),
    )
    ids = [t.rule_id for t in propose_keeper_tickets(_fixture(extras=extras), _cfg(), briefing)]
    assert "keeper_fade_dog" in ids


def test_dog_never_beat_parses_h2h():
    assert dog_never_beat(("Sep 2025 Arsenal 2–0 Coventry",), "Arsenal FC", "Coventry City FC")
    assert not dog_never_beat(("Sep 2025 Coventry 1–0 Arsenal",), "Arsenal FC", "Coventry City FC")
    assert not dog_never_beat((), "Arsenal FC", "Coventry City FC")


def test_non_watchlist_emits_nothing():
    extras = (
        _binary("O/U 0.5 Goals", "totals", 0.5, 0.94),
        _binary("Total corners O/U 7.5", "totals", 7.5, 0.75, slug="corners-75"),
    )
    fixture = _fixture(home="Burnley FC", away="Luton Town FC", home_yes=0.40, away_yes=0.30, extras=extras)
    assert propose_keeper_tickets(fixture, _cfg(), _briefing("Burnley FC", "Luton Town FC")) == []


def test_corners_skipped_when_not_two_favorites():
    extras = (_binary("Total corners O/U 7.5", "totals", 7.5, 0.75, slug="corners-75"),)
    ids = [t.rule_id for t in propose_keeper_tickets(_fixture(extras=extras), _cfg(), _briefing())]
    assert "keeper_corners_7_5" not in ids


def test_classifier_helpers():
    assert is_neither_first_to_score(_binary("Neither team to score first?", "other", None, 0.1))
    assert is_exact_score_00(_binary("Exact Score 0-0", "other", None, 0.1))
    assert is_any_other_score(_binary("Exact Score: Any Other Score", "other", None, 0.1))
    assert is_over_goals(_binary("O/U 0.5 Goals", "totals", 0.5, 0.96), 0.5)
    assert is_over_goals(
        _binary("Club Atlético de Madrid vs. Real Madrid CF: O/U 0.5", "totals", 0.5, 0.96),
        0.5,
    )
    assert not is_over_goals(
        _binary(
            "Club Atlético de Madrid vs. Real Madrid CF: Club Atlético de Madrid 1st Half O/U 0.5",
            "totals",
            0.5,
            0.49,
        ),
        0.5,
    )
    assert not is_over_goals(
        _binary(
            "Club Atlético de Madrid vs. Real Madrid CF: Club Atlético de Madrid O/U 0.5",
            "soccer_team_totals",
            0.5,
            0.62,
        ),
        0.5,
    )


def test_over_ignores_first_half_and_team_totals():
    extras = (
        _binary(
            "Club Atlético de Madrid vs. Real Madrid CF: Club Atlético de Madrid 1st Half O/U 0.5",
            "totals",
            0.5,
            0.49,
            slug="atm-1h",
        ),
        _binary(
            "Club Atlético de Madrid vs. Real Madrid CF: Club Atlético de Madrid O/U 0.5",
            "soccer_team_totals",
            0.5,
            0.62,
            slug="atm-team",
        ),
        _binary(
            "Club Atlético de Madrid vs. Real Madrid CF: O/U 0.5",
            "totals",
            0.5,
            0.96,
            slug="match-ou",
        ),
    )
    fixture = _fixture(
        home="Club Atlético de Madrid",
        away="Real Madrid CF",
        home_yes=0.28,
        away_yes=0.48,
        extras=extras,
        league="laliga",
    )
    briefing = _briefing(home="Club Atlético de Madrid", away="Real Madrid CF")
    tickets = propose_keeper_tickets(fixture, _cfg(), briefing)
    over = next(t for t in tickets if t.rule_id == "keeper_over_0_5")
    assert "1st Half" not in over.question
    assert "Club Atlético de Madrid O/U" not in over.question
    assert over.question.endswith("O/U 0.5")
    assert over.price >= 0.95


def test_over_does_not_buy_half_book_when_full_match_missing():
    extras = (
        _binary(
            "Club Atlético de Madrid vs. Real Madrid CF: Club Atlético de Madrid 1st Half O/U 0.5",
            "totals",
            0.5,
            0.49,
            slug="atm-1h",
        ),
    )
    fixture = _fixture(
        home="Club Atlético de Madrid",
        away="Real Madrid CF",
        home_yes=0.28,
        away_yes=0.48,
        extras=extras,
        league="laliga",
    )
    ids = [
        t.rule_id
        for t in propose_keeper_tickets(fixture, _cfg(), _briefing("Club Atlético de Madrid", "Real Madrid CF"))
    ]
    assert "keeper_over_0_5" not in ids
    assert "keeper_over_1_5" not in ids


def test_place_keeper_book_dry_run_does_not_post():
    from pm_football_bot.config import load_settings

    extras = (_binary("O/U 0.5 Goals", "totals", 0.5, 0.94),)
    tickets = propose_keeper_tickets(_fixture(home_yes=0.88, extras=extras), _cfg(), _briefing())
    assert tickets
    out = place_keeper_book(tickets, load_settings(), live=False)
    assert out["mode"] == "dry_run"
    assert out["placed"] == []
    assert out["fatal"] is None


def test_open_tickets_excludes_placed(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEPER_STATE_PATH", str(tmp_path / "placed.json"))
    extras = (
        _binary("O/U 0.5 Goals", "totals", 0.5, 0.94),
        _binary("O/U 5.5 Goals", "totals", 5.5, 0.12),
    )
    tickets = propose_keeper_tickets(_fixture(home_yes=0.88, extras=extras), _cfg(), _briefing())
    assert len(tickets) >= 2
    save_placed({ticket_key(tickets[0])})
    remaining = open_tickets(tickets)
    assert ticket_key(tickets[0]) not in {ticket_key(row) for row in remaining}
    assert len(remaining) == len(tickets) - 1


def test_placed_list_migrates_and_cancelled_can_rebuy(tmp_path, monkeypatch):
    path = tmp_path / "placed.json"
    monkeypatch.setenv("KEEPER_STATE_PATH", str(path))
    extras = (_binary("O/U 0.5 Goals", "totals", 0.5, 0.94),)
    tickets = propose_keeper_tickets(_fixture(home_yes=0.88, extras=extras), _cfg(), _briefing())
    key = ticket_key(tickets[0])
    path.write_text(f'["{key}"]', encoding="utf-8")
    book = load_placed_book()
    assert book[key].status == "unknown"
    assert key in load_placed()
    book[key].status = "cancelled"
    save_placed_book(book)
    assert key not in load_placed()
    remaining = open_tickets(tickets)
    assert ticket_key(tickets[0]) in {ticket_key(row) for row in remaining}


def test_order_id_and_clob_status():
    assert order_id_from_result({"orderID": "0xabc"}) == "0xabc"
    assert order_id_from_result({"order": {"id": "0xdef"}}) == "0xdef"
    assert classify_clob_status({"status": "LIVE", "original_size": "5", "size_matched": "0"}) == "live"
    assert classify_clob_status({"status": "LIVE", "original_size": "5", "size_matched": "2"}) == "partial"
    assert classify_clob_status({"status": "MATCHED", "original_size": "5", "size_matched": "5"}) == "filled"
    assert classify_clob_status({"status": "live", "transactionsHashes": ["0x1"]}) == "filled"
    assert classify_clob_status({"status": "CANCELED", "size_matched": "0"}) == "cancelled"


def test_refresh_marks_open_and_filled_orders(tmp_path, monkeypatch):
    from pm_football_bot.config import load_settings

    path = tmp_path / "placed.json"
    monkeypatch.setenv("KEEPER_STATE_PATH", str(path))
    live = PlacedOrder(key="a:over", status="live", order_id="ord-live", token_id="tok-live", shares=5)
    filled = PlacedOrder(key="b:under", status="live", order_id="ord-fill", token_id="tok-fill", shares=5)
    save_placed_book({"a:over": live, "b:under": filled})

    class _Client:
        def get_open_orders(self):
            return [{"id": "ord-live", "status": "LIVE", "asset_id": "tok-live", "original_size": "5", "size_matched": "1"}]

        def get_order(self, order_id: str):
            assert order_id == "ord-fill"
            return {"id": "ord-fill", "status": "MATCHED", "original_size": "5", "size_matched": "5"}

        def get_trades(self, only_first_page: bool = False):
            return []

    book = refresh_placed_orders(load_settings(), client=_Client())
    assert book["a:over"].status == "partial"
    assert book["a:over"].size_matched == 1
    assert book["b:under"].status == "filled"
