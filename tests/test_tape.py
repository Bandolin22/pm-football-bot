from datetime import datetime, timezone

from pm_football_bot.tape import (
    Fill,
    classify_kind,
    fill_from_trade,
    infer_favorite,
    summarize,
    tag_fills,
)

KO = datetime(2026, 8, 19, 19, 0, tzinfo=timezone.utc)


def _fill(
    title: str,
    outcome: str,
    price: float,
    *,
    minutes: float = -60,
    usd: float | None = None,
    condition: str = "c1",
    shares: float | None = None,
) -> Fill:
    ts = datetime.fromtimestamp(KO.timestamp() + minutes * 60, tz=timezone.utc)
    size = shares if shares is not None else (usd / price if usd else 100)
    raw = {
        "side": "BUY",
        "timestamp": int(ts.timestamp()),
        "size": size,
        "price": price,
        "title": title,
        "outcome": outcome,
        "eventSlug": "lal-mad-mala-2026-08-19",
        "conditionId": condition,
        "transactionHash": f"{title}-{outcome}-{minutes}-{price}",
    }
    fill = fill_from_trade(raw, KO)
    assert fill is not None
    return fill


def test_classify_moneyline_and_game_total():
    win = classify_kind("Will Club Atlético de Madrid win on 2026-08-19?", "Yes")
    assert win.family == "moneyline" and win.side == "yes"
    dog = classify_kind("Will Málaga CF win on 2026-08-19?", "No")
    assert dog.family == "moneyline" and dog.side == "no"
    game = classify_kind("Club Atlético de Madrid vs. Málaga CF: O/U 0.5", "Over")
    assert game.family == "game_ou" and game.side == "over" and game.line == 0.5
    team = classify_kind("Club Atlético de Madrid vs. Málaga CF: Málaga CF O/U 1.5", "Under")
    assert team.family == "team_ou" and team.side == "under"
    draw = classify_kind("Will Club Atlético de Madrid vs. Málaga CF end in a draw?", "Yes")
    assert draw.family == "draw" and draw.side == "yes"


def test_harvest_pre_dog_no():
    fills = [
        _fill("Will Málaga CF win on 2026-08-19?", "No", 0.91, minutes=-1440, usd=6200),
        _fill("Club Atlético de Madrid vs. Málaga CF: O/U 0.5", "Over", 0.94, minutes=-2000, usd=200),
        _fill("Club Atlético de Madrid vs. Málaga CF: O/U 5.5", "Under", 0.92, minutes=-1800, usd=1100),
    ]
    tag_fills(fills, "Club Atlético de Madrid", "Málaga CF")
    assert [row.edge for row in fills] == ["harvest", "harvest", "harvest"]
    assert all(not row.live for row in fills)


def test_gap_favorite_yes_in_play():
    fill = _fill("Will Club Atlético de Madrid win on 2026-08-19?", "Yes", 0.52, minutes=26, usd=3257)
    tag_fills([fill], "Club Atlético de Madrid", "Málaga CF")
    assert fill.live
    assert fill.edge == "gap"


def test_clock_draw_and_unders():
    draws = _fill(
        "Will Club Atlético de Madrid vs. Málaga CF end in a draw?",
        "Yes",
        0.36,
        minutes=91,
        usd=250,
    )
    under = _fill(
        "Club Atlético de Madrid vs. Málaga CF: O/U 2.5",
        "Under",
        0.61,
        minutes=70,
        usd=1000,
        condition="ou",
    )
    no = _fill("Will Club Atlético de Madrid win on 2026-08-19?", "No", 0.45, minutes=90, usd=3000)
    tag_fills([draws, under, no], "Club Atlético de Madrid", "Málaga CF")
    assert draws.edge == "clock"
    assert under.edge == "clock"
    assert no.edge == "other"


def test_tail_dog_yes():
    fill = _fill("Will Málaga CF win on 2026-08-19?", "Yes", 0.13, minutes=21, usd=3083)
    tag_fills([fill], "Club Atlético de Madrid", "Málaga CF")
    assert fill.edge == "tail"


def test_flip_after_goal_high_yes():
    fill = _fill("Will Club Atlético de Madrid win on 2026-08-19?", "Yes", 0.87, minutes=101, usd=24800)
    tag_fills([fill], "Club Atlético de Madrid", "Málaga CF")
    assert fill.edge == "flip"


def test_pair_yes_and_no_same_minute():
    yes = _fill(
        "Will Club Atlético de Madrid win on 2026-08-19?",
        "Yes",
        0.52,
        minutes=86,
        usd=3200,
        condition="atleti-win",
    )
    no = _fill(
        "Will Club Atlético de Madrid win on 2026-08-19?",
        "No",
        0.45,
        minutes=86.2,
        usd=2400,
        condition="atleti-win",
    )
    tag_fills([yes, no], "Club Atlético de Madrid", "Málaga CF")
    assert yes.paired and no.paired
    assert yes.edge == "gap"
    assert no.edge == "pair"


def test_infer_favorite_from_pre_yes():
    fills = [
        _fill("Will Club Atlético de Madrid win on 2026-08-19?", "Yes", 0.73, minutes=-1440, usd=6500),
        _fill("Will Málaga CF win on 2026-08-19?", "Yes", 0.10, minutes=-1400, usd=1800),
    ]
    favorite, dog = infer_favorite(fills, "Club Atlético de Madrid", "Málaga CF")
    assert favorite and "Atlético" in favorite
    assert dog and "Málaga" in dog


def test_summarize_splits_pre_and_live():
    fills = [
        _fill("Will Málaga CF win on 2026-08-19?", "No", 0.91, minutes=-60, usd=100),
        _fill("Will Club Atlético de Madrid win on 2026-08-19?", "Yes", 0.52, minutes=20, usd=200),
    ]
    tag_fills(fills, "Club Atlético de Madrid", "Málaga CF")
    summary = summarize(fills)
    assert summary.pre_n == 1 and summary.live_n == 1
    assert summary.pre_usd == 100
    assert summary.live_usd == 200
    assert summary.by_edge["harvest"] == 100
    assert summary.by_edge["gap"] == 200
