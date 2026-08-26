from pm_football_bot.gamma import is_primary_moneyline_event
from pm_football_bot.models import Ticket
from pm_football_bot.report import review_ticket


def test_drops_first_team_to_score_events():
    assert is_primary_moneyline_event({"title": "Arsenal FC vs. Coventry City FC", "slug": "epl-ars-cov-2026-08-21"})
    assert not is_primary_moneyline_event(
        {"title": "Arsenal FC vs. Coventry City FC - First Team to Score", "slug": "epl-ars-cov-2026-08-21-first-team-to-score"}
    )
    assert not is_primary_moneyline_event(
        {"title": "Hull City AFC vs. Manchester United FC - Halftime Result", "slug": "epl-hul-mun-2026-08-22-halftime-result"}
    )
    assert not is_primary_moneyline_event(
        {"title": "Hull City AFC vs. Manchester United FC - Total Corners", "slug": "epl-hul-mun-2026-08-22-total-corners"}
    )
    assert not is_primary_moneyline_event(
        {"title": "Elche CF vs. FC Barcelona - Player Props", "slug": "lal-elc-bar-2026-08-23-player-props"}
    )


def test_reviews_first_scorer_as_skip():
    ticket = Ticket(
        league="epl",
        fixture="Arsenal FC vs. Coventry City FC - First Team to Score",
        slug="x",
        rule_id="fade_dog",
        question="Neither",
        token_id="1",
        outcome="No",
        price=0.94,
        shares=19,
        cost_usd=18,
        spread=0.01,
        reason="mismatch Arsenal FC 0.795 vs Neither 0.055",
        dog_yes=0.055,
        favorite_yes=0.795,
    )
    assert review_ticket(ticket).verdict == "skip"


def test_reviews_yaml_mismatch_as_keep():
    ticket = Ticket(
        league="epl",
        fixture="Hull City AFC vs. Manchester United FC",
        slug="epl-hul-mun-2026-08-22",
        rule_id="fade_dog",
        question="Hull No",
        token_id="1",
        outcome="No",
        price=0.90,
        shares=20,
        cost_usd=18,
        spread=0.01,
        reason="mismatch Manchester United FC 0.715 vs Hull City AFC 0.095",
        dog_yes=0.095,
        favorite_yes=0.715,
        dog_team="Hull City AFC",
        favorite_team="Manchester United FC",
    )
    assert review_ticket(ticket).verdict == "keep"


def test_reviews_athletic_style_as_keep():
    ticket = Ticket(
        league="laliga",
        fixture="FC Barcelona vs. Athletic Club",
        slug="y",
        rule_id="fade_dog",
        question="Athletic No",
        token_id="1",
        outcome="No",
        price=0.87,
        shares=20,
        cost_usd=18,
        spread=0.03,
        reason="mismatch FC Barcelona 0.705 vs Athletic Club 0.115",
        dog_yes=0.115,
        favorite_yes=0.705,
        dog_team="Athletic Club",
        favorite_team="FC Barcelona",
    )
    assert review_ticket(ticket).verdict == "keep"


def test_reviews_even_game_as_borderline():
    ticket = Ticket(
        league="epl",
        fixture="Everton FC vs. Crystal Palace FC",
        slug="z",
        rule_id="fade_dog",
        question="Everton No",
        token_id="1",
        outcome="No",
        price=0.90,
        shares=20,
        cost_usd=18,
        spread=0.01,
        reason="mismatch Crystal Palace FC 0.415 vs Everton FC 0.305",
        dog_yes=0.305,
        favorite_yes=0.415,
    )
    assert review_ticket(ticket).verdict == "borderline"
