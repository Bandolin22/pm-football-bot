from pm_football_bot.config import load_settings
from pm_football_bot.models import Ticket
from pm_football_bot.risk import size_book


def _ticket(slug: str, price: float, cost: float, rule: str = "fade_dog") -> Ticket:
    return Ticket(
        league="epl",
        fixture=slug,
        slug=slug,
        rule_id=rule,
        question=slug,
        token_id="1",
        outcome="No",
        price=price,
        shares=20,
        cost_usd=cost,
        spread=0.01,
        reason="test",
    )


def test_risk_cap_keeps_highest_prices_under_open_limit():
    settings = load_settings()
    tickets = [
        _ticket("a", 0.96, 200),
        _ticket("b", 0.90, 200),
        _ticket("c", 0.86, 200),
    ]
    kept = size_book(tickets, settings)
    assert [t.slug for t in kept] == ["a"]
    assert sum(t.cost_usd for t in kept) <= settings.max_open_usd
