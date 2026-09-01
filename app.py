from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "src"))

from datetime import timezone

import streamlit as st

from pm_football_bot.report import (
    ReviewedTicket,
    group_by_fixture,
    planned_usd,
    profit_if_all_win,
)
from pm_football_bot.predict import forecast_match, model_ready
from pm_football_bot.predict.explain import local_explanation
from pm_football_bot.scan import collect_scan
from pm_football_bot.scout import Briefing, TeamPulse, load_briefing
from pm_football_bot.simulate import (
    KEY_SCORES,
    lot_results,
    scoreline,
    simulate_book,
)
from pm_football_bot.swisstony import (
    PROFILE,
    SWISSTONY,
    SwissLot,
    load_book,
    lots_for_parent,
    matched_lot,
    parent_slug,
)
from pm_football_bot.tape import (
    EDGE_LABEL,
    MatchTape,
    fixture_options,
    load_tape,
)

st.set_page_config(page_title="Harvest desk", layout="wide")

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px; padding-top: 1.5rem; }
      .ticket {
        border: 1px solid #e6e6e6;
        border-radius: 12px;
        padding: 14px 16px;
        margin-bottom: 10px;
        background: #fff;
      }
      .ticket.keep { border-left: 4px solid #1f8a4c; }
      .ticket.skip { border-left: 4px solid #b42318; background: #fafafa; }
      .ticket.borderline { border-left: 4px solid #b54708; }
      .ticket.swiss { border-left: 4px solid #1d4ed8; }
      .ticket.match { background: #f3f8ff; }
      .chip {
        display: inline-block;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0.02em;
        padding: 2px 8px;
        border-radius: 999px;
        margin-right: 6px;
      }
      .chip.keep { background: #e8f6ee; color: #17663a; }
      .chip.skip { background: #fde8e6; color: #912018; }
      .chip.borderline { background: #fef4e6; color: #93370d; }
      .chip.swiss { background: #e8eefc; color: #1d4ed8; }
      .muted { color: #667085; font-size: 14px; }
      .price { font-variant-numeric: tabular-nums; }
    </style>
    """,
    unsafe_allow_html=True,
)

RULE_LABEL = {
    "fade_dog": "Dog does not win",
    "over_0_5": "Someone scores (Over 0.5)",
    "under_5_5": "Under 6 goals (Under 5.5)",
}

LEAGUE_LABEL = {
    "epl": "Premier League",
    "laliga": "LaLiga",
    "ligue1": "Ligue 1",
    "seriea": "Serie A",
    "bundesliga": "Bundesliga",
    "ucl": "UCL",
    "uel": "Europa League",
    "col": "Conference League",
    "efl": "EFL Cup",
    "elc": "Championship",
    "efa": "FA Cup",
    "cdr": "Copa del Rey",
    "dfb": "DFB-Pokal",
    "itc": "Coppa Italia",
    "cde": "Coupe de France",
    "ssc": "Supercopa",
    "isc": "Supercoppa",
    "gsc": "German Super Cup",
    "frtc": "Trophée des Champions",
    "usc": "UEFA Super Cup",
    "cwc": "Club World Cup",
    "ecs": "Community Shield",
}


def _kickoff(row: ReviewedTicket) -> str:
    ko = row.ticket.kickoff
    if ko is None:
        return "Kickoff unknown"
    return ko.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")


def _poly_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"


def render_ticket(row: ReviewedTicket) -> None:
    t = row.ticket
    label = RULE_LABEL.get(t.rule_id, t.rule_id)
    win = row.if_win_usd
    profit = row.profit_if_win_usd
    spread = f"{t.spread:.3f}" if t.spread is not None else "—"
    st.markdown(
        f"""
        <div class="ticket {row.verdict}">
          <span class="chip {row.verdict}">{row.verdict.upper()}</span>
          <strong>{label}</strong>
          <span class="muted"> · buy {t.outcome}</span>
          <div style="margin-top:8px" class="price">
            You: <strong>{t.shares:.2f} shares</strong> at {t.price * 100:.1f}¢
            · pay ${t.cost_usd:.2f}
            · if it hits, back ~${win:.2f} (about +${profit:.2f})
          </div>
          <div class="muted" style="margin-top:6px">{t.meaning} {row.why}</div>
          <div class="muted">Spread {spread} · {t.question}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_swiss_lot(lot: SwissLot, *, matched: bool = False) -> None:
    now = f"{lot.cur_price * 100:.1f}¢" if lot.cur_price is not None else "—"
    extra = "match" if matched else ""
    chip = "SAME LINE" if matched else "HIS BOOK"
    st.markdown(
        f"""
        <div class="ticket swiss {extra}">
          <span class="chip swiss">{chip}</span>
          <strong>{lot.title}</strong>
          <span class="muted"> · {lot.outcome}</span>
          <div style="margin-top:8px" class="price">
            Swisstony: <strong>{lot.shares_bought:,.1f} shares bought</strong>
            at {lot.avg_cents:.1f}¢ · cost ${lot.cost_usd:,.0f}
            · now {now} · uPnL ${lot.u_pnl:,.0f}
          </div>
          <div class="muted">{lot.shares_now:,.1f} shares still open</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _load_swiss(progress=None) -> list[SwissLot]:
    if progress:
        progress.write("Loading swisstony’s open buys…")
    return load_book()


def _num(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _pulse_block(label: str, pulse: TeamPulse | None) -> None:
    st.markdown(f"**{label}**")
    if pulse is None:
        st.caption("No table row.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Table", f"{_num(pulse.position, 0)}" if pulse.position is not None else "—")
    c2.metric("Pts", _num(pulse.points, 0))
    c3.metric("Form", pulse.form or "—")
    c4.metric("Rest", f"{pulse.rest_days}d" if pulse.rest_days is not None else "—")
    st.caption(
        f"Home PPG {_num(pulse.home_ppg)} · Away PPG {_num(pulse.away_ppg)} · "
        f"GF/GA {_num(pulse.gf_pg)}/{_num(pulse.ga_pg)} · GD {_num(pulse.goal_diff, 0)}"
    )
    for line in pulse.last_five:
        st.caption(line)
    if pulse.next_match:
        st.caption(f"Next: {pulse.next_match}")


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}"


def render_forecast(ticket) -> None:
    st.markdown("##### 1X2 engine")
    st.caption(
        "Leakage-safe ensemble (form / ELO / shot xG proxy) blended with live Polymarket. "
        "Second view on KEEP — not a reason to buy a winner. "
        "Train with `python -m pm_football_bot.predict.train`."
    )
    fkey = f"forecast:{ticket.slug}"
    forecast = st.session_state.get(fkey)
    if forecast is None:
        if not model_ready():
            st.caption("No trained model yet. Run `python -m pm_football_bot.predict.train`.")
        return
    if forecast.error:
        st.warning(forecast.error)
        return
    blended = forecast.blended
    c1, c2, c3 = st.columns(3)
    c1.metric("Home", _pct(None if blended is None else blended.home))
    c2.metric("Draw", _pct(None if blended is None else blended.draw))
    c3.metric("Away", _pct(None if blended is None else blended.away))
    st.caption(
        f"Blended pick **{forecast.pick}** · matched as "
        f"{forecast.matched_home} vs {forecast.matched_away}."
    )
    rows = []
    if forecast.ml is not None:
        rows.append(f"ML {_pct(forecast.ml[0])} / {_pct(forecast.ml[1])} / {_pct(forecast.ml[2])}")
    if forecast.poly is not None:
        rows.append(
            f"Polymarket {_pct(forecast.poly[0])} / {_pct(forecast.poly[1])} / {_pct(forecast.poly[2])}"
        )
    for line in rows:
        st.caption(line)
    for gap in forecast.gaps:
        st.info(gap)
    st.caption(local_explanation(forecast))


def render_briefing(ticket) -> None:
    st.markdown("##### Team briefing")
    st.caption(
        "Form, table, home/away, H2H, and rest from football-data.org. "
        "Opta predicted XI / injuries / xG are **not** on a public API — those stay missing unless you buy Stats Perform."
    )
    key = f"briefing:{ticket.slug}"
    fkey = f"forecast:{ticket.slug}"
    if st.button("Load briefing", key=f"btn-{ticket.slug}"):
        with st.spinner("Loading football-data.org…"):
            st.session_state[key] = load_briefing(
                ticket.league,
                ticket.fixture,
                ticket.kickoff,
                ticket.favorite_team or "",
            )
        if model_ready():
            with st.spinner("Scoring 1X2…"):
                st.session_state[fkey] = forecast_match(
                    ticket.fixture,
                    favorite_team=ticket.favorite_team or "",
                    favorite_yes=ticket.favorite_yes,
                    dog_yes=ticket.dog_yes,
                    kickoff=ticket.kickoff,
                )
    briefing: Briefing | None = st.session_state.get(key)
    if briefing is None:
        render_forecast(ticket)
        return
    if briefing.error:
        st.warning(briefing.error)
        render_forecast(ticket)
        return
    left, right = st.columns(2)
    with left:
        _pulse_block(f"Home · {briefing.home_name}", briefing.home)
    with right:
        _pulse_block(f"Away · {briefing.away_name}", briefing.away)
    if briefing.h2h:
        st.markdown("**Head to head**")
        for line in briefing.h2h:
            st.caption(line)
    else:
        st.caption("No recent H2H in the pulled results.")
    if briefing.vetoes:
        for note in briefing.vetoes:
            st.warning(note)
    else:
        st.success("No automatic fade_dog veto from table/home-away/rest.")
    render_forecast(ticket)
    with st.expander("What this briefing cannot show"):
        for item in briefing.missing:
            st.caption(f"• {item}")


st.title("Harvest desk")
st.caption(
    "Your dry-run harvest on EPL, LaLiga, Ligue 1, Serie A, and Bundesliga. "
    "Swisstony’s open book is still mostly EPL / LaLiga / UCL. This page never places orders. "
    "Open **Upcoming** in the left sidebar for every watchlist 1X2, including cups "
    "(EFL, FA Cup, UCL, Europa, DFB-Pokal). Open **My trading** for your "
    "[@zerobetap](https://polymarket.com/@zerobetap) history and PnL by club and harvest factor."
)

with st.expander("How to read this", expanded=False):
    st.markdown(
        """
        **Your plan** is Layer A only: the 3-ticket harvest on mismatches
        (dog No, Over 0.5, Under 5.5). That is what you can copy with $400.
        Open a KEEP accordion and press **Load briefing** for form, table,
        home/away, H2H, rest, and the 1X2 engine (after
        `python -m pm_football_bot.predict.train`). That is not Opta xG or a predicted XI.

        **Compare** lines up the same match: your tickets next to his **open**
        lots. Open lots are mostly Layer A. They miss most of his in-play book.

        **Live tape** loads his actual buy fills and splits them at kickoff.
        Tags are explanations, not KEEP signals: Harvest, Gap (crashed favorite
        Yes), Clock (Draw / Nos / Under on a stuck score), Pair (Yes+No ≈ $1),
        Flip (after a goal, high-cent side), Tail (cheap dog Yes).

        His size is not a target. Do not copy Gap/Flip fills after you see them.
        """
    )

left, right = st.columns([1, 1])
with left:
    scan = st.button("Scan live boards", type="primary", use_container_width=True)
with right:
    st.caption("Loads your harvest list and swisstony’s open positions. About 1–2 minutes.")

if scan:
    progress = st.status("Scanning boards and swisstony’s wallet…", expanded=True)

    def _note(msg: str) -> None:
        progress.write(msg)

    try:
        st.session_state["scan"] = collect_scan(on_progress=_note)
        st.session_state["swiss"] = _load_swiss(progress)
        st.session_state.pop("swiss_error", None)
        progress.update(label="Scan finished", state="complete")
    except Exception as exc:
        progress.update(label="Scan failed", state="error")
        st.error(str(exc))

result = st.session_state.get("scan")
if result is None:
    st.info("Press **Scan live boards** to load your harvest list and swisstony’s buys.")
    st.stop()

if "swiss" not in st.session_state:
    try:
        with st.spinner("Loading swisstony’s open buys…"):
            st.session_state["swiss"] = _load_swiss()
        st.session_state.pop("swiss_error", None)
    except Exception as exc:
        st.session_state["swiss"] = []
        st.session_state["swiss_error"] = str(exc)

swiss: list[SwissLot] = st.session_state.get("swiss") or []
swiss_error = st.session_state.get("swiss_error")

reviewed = result.reviewed
keep = result.by_verdict("keep")
skip = result.by_verdict("skip")
border = result.by_verdict("borderline")
keep_usd = planned_usd(keep)
skip_usd = planned_usd(skip) + planned_usd(border)
swiss_usd = sum(lot.cost_usd for lot in swiss)
swiss_shares = sum(lot.shares_bought for lot in swiss)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Matches scanned", f"{result.scanned}")
m2.metric("Your keep", f"${keep_usd:.0f}")
m3.metric("If all keeps hit", f"+${profit_if_all_win(keep):.0f}")
m4.metric("Swisstony open book", f"${swiss_usd:,.0f}")
m5.metric("His bought shares", f"{swiss_shares:,.0f}")

st.caption(
    "The swisstony dollar figure is **open inventory**, not locked profit, and "
    "not his full match P&L. **Simulation** prices that open book on FT scores. "
    "**Live tape** shows pre-match vs in-play *buys* (Layer A vs Layer B)."
)

st.caption(
    f"Dry-run · your bankroll ${result.settings.bankroll_usd:.0f} · "
    f"${result.settings.ticket_usd:.0f} / ticket · "
    f"[swisstony]({PROFILE}) `{SWISSTONY[:6]}…{SWISSTONY[-4:]}` · "
    f"{result.as_of.strftime('%Y-%m-%d %H:%M UTC')}"
)

if swiss_error:
    st.error(f"Could not load swisstony’s book: {swiss_error}")

if skip_usd:
    st.warning(
        f"${skip_usd:.0f} of *your* list is skip or borderline. "
        "Compare using KEEP tickets, not the raw dump."
    )

plan_tab, compare_tab, tape_tab, sim_tab, book_tab = st.tabs(
    ["Your plan", "Compare with swisstony", "Live tape", "Simulation", "His open book"]
)

with plan_tab:
    if not reviewed:
        st.success("No liquid mismatch tickets in the current window.")
    else:
        for _, rows in group_by_fixture(reviewed):
            first = rows[0].ticket
            verdicts = {r.verdict for r in rows}
            if "keep" in verdicts:
                badge = "KEEP"
            elif "borderline" in verdicts:
                badge = "BORDERLINE"
            else:
                badge = "SKIP"
            dog_pct = f"{first.dog_yes * 100:.1f}¢" if first.dog_yes is not None else "—"
            fav_pct = f"{first.favorite_yes * 100:.1f}¢" if first.favorite_yes is not None else "—"
            league = LEAGUE_LABEL.get(first.league, first.league)
            header = f"{badge} · {league} · {first.fixture} · {len(rows)} tickets"
            with st.expander(header, expanded=("keep" in verdicts)):
                st.markdown(
                    f"**{first.favorite_team or 'Favorite'}** to win {fav_pct} vs "
                    f"**{first.dog_team or 'Dog'}** {dog_pct} · {_kickoff(rows[0])}"
                )
                st.link_button("Open on Polymarket", _poly_url(first.slug))
                for row in rows:
                    render_ticket(row)
                if "keep" in verdicts:
                    render_briefing(first)

        st.markdown(
            f"**Suggested plan:** KEEP only (**${keep_usd:.0f}**). "
            f"If every one wins, about **+${profit_if_all_win(keep):.0f}**. "
            f"One dog win is −${result.settings.ticket_usd:.0f}."
        )

with compare_tab:
    if not swiss:
        st.info("Swisstony’s book did not load. Re-run the scan.")
    else:
        st.markdown(
            "Each match: **you** on the left, **his open lots** on the right. "
            "Blue **SAME LINE** is the harvest ticket he also holds. "
            "Open lots are mostly **Layer A**. For in-play fills, use **Live tape**."
        )
        for _, rows in group_by_fixture(reviewed):
            first = rows[0].ticket
            his = lots_for_parent(swiss, first.slug)
            his_cost = sum(lot.cost_usd for lot in his)
            his_sh = sum(lot.shares_bought for lot in his)
            league = LEAGUE_LABEL.get(first.league, first.league)
            label = (
                f"{league} · {first.fixture} · you ${sum(r.ticket.cost_usd for r in rows):.0f} · "
                f"him ${his_cost:,.0f} ({his_sh:,.0f} sh)"
            )
            with st.expander(label, expanded=any(r.verdict == "keep" for r in rows)):
                you_col, him_col = st.columns(2)
                with you_col:
                    st.subheader("You")
                    for row in rows:
                        render_ticket(row)
                        hit = matched_lot(row.ticket, his)
                        if hit is None:
                            st.caption("He has no open lot on this exact line (or it is too small to show).")
                        else:
                            ratio = hit.shares_bought / row.ticket.shares if row.ticket.shares else 0
                            st.caption(
                                f"He bought {hit.shares_bought:,.0f} shares vs your {row.ticket.shares:.1f} "
                                f"({ratio:,.0f}×). Copy the line, not the size."
                            )
                with him_col:
                    st.subheader("Swisstony")
                    if not his:
                        st.caption("No open lots on this fixture slug.")
                    else:
                        pinned = []
                        rest = []
                        matched_ids = set()
                        for row in rows:
                            hit = matched_lot(row.ticket, his)
                            if hit is not None:
                                matched_ids.add(id(hit))
                        for lot in his:
                            if id(lot) in matched_ids:
                                pinned.append(lot)
                            else:
                                rest.append(lot)
                        for lot in pinned:
                            render_swiss_lot(lot, matched=True)
                        for lot in rest[:12]:
                            render_swiss_lot(lot)
                        if len(rest) > 12:
                            st.caption(f"{len(rest) - 12} more of his lots hidden on this match.")

        your_keys = {parent_slug(row.ticket.slug) for row in reviewed}
        extra_parents = sorted(
            {lot.parent for lot in swiss if lot.parent not in your_keys},
            key=lambda key: -sum(lot.cost_usd for lot in lots_for_parent(swiss, key)),
        )
        if extra_parents:
            st.markdown("### He is in these matches — you are not")
            st.caption("Usually even games or tickets that failed the 85–97¢ / spread filter.")
            for key in extra_parents[:15]:
                his = lots_for_parent(swiss, key)
                title = next((lot.title.split(":")[0] for lot in his if " vs" in lot.title.lower()), key)
                cost = sum(lot.cost_usd for lot in his)
                with st.expander(f"{his[0].league.upper()} · {title} · him ${cost:,.0f}"):
                    for lot in his[:10]:
                        render_swiss_lot(lot)


def _tape_choices(reviewed: list[ReviewedTicket], swiss: list[SwissLot]) -> list[str]:
    extra = [row.ticket.slug for row in reviewed]
    parents = [lot.parent for lot in swiss]
    return fixture_options(parents, extra)


def _render_tape_fill_table(tape: MatchTape) -> None:
    rows = [
        {
            "utc": fill.utc.strftime("%Y-%m-%d %H:%M:%S"),
            "window": fill.window,
            "edge": EDGE_LABEL.get(fill.edge, fill.edge),
            "paired": "yes" if fill.paired else "",
            "kind": fill.kind.label,
            "side": fill.outcome,
            "¢": fill.cents,
            "$": round(fill.usd, 2),
            "shares": round(fill.shares, 1),
            "market": fill.title,
        }
        for fill in tape.fills
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True, height=420)
    st.caption(f"{len(rows)} buy fills. All recorded as BUY — he completes the other side instead of selling.")


def _render_tape(tape: MatchTape) -> None:
    summary = tape.summary
    ko = (
        tape.kickoff.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
        if tape.kickoff
        else "unknown"
    )
    status = "ended" if tape.ended else ("live now" if tape.live_now else "upcoming")
    st.markdown(
        f"**{tape.title}** · {status}"
        + (f" · FT {tape.score}" if tape.score else "")
        + f" · KO {ko}"
    )
    if tape.favorite or tape.dog:
        st.caption(
            f"Favorite (from his pre-match Yes VWAP): **{tape.favorite or '—'}** · "
            f"dog: **{tape.dog or '—'}**. Tags use that split."
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pre-match buys", f"${summary.pre_usd:,.0f}", f"{summary.pre_n} fills")
    c2.metric("In-play buys", f"${summary.live_usd:,.0f}", f"{summary.live_n} fills")
    share = 0.0
    total = summary.pre_usd + summary.live_usd
    if total:
        share = 100 * summary.live_usd / total
    c3.metric("Live share of spend", f"{share:.0f}%")
    if tape.realized_pnl is not None:
        c4.metric("Realized P&L", f"${tape.realized_pnl:,.0f}")
    else:
        c4.metric("Realized P&L", "open")
    st.link_button("Open match on Polymarket", tape.event_url)

    if summary.by_edge:
        st.subheader("Spend by Layer B tag")
        try:
            import pandas as pd

            edge_df = pd.DataFrame(
                {
                    "tag": [EDGE_LABEL.get(k, k) for k in summary.by_edge],
                    "$": list(summary.by_edge.values()),
                }
            ).set_index("tag")
            st.bar_chart(edge_df, height=220)
        except ImportError:
            st.write(summary.by_edge)

    fam_rows = []
    families = sorted(set(summary.by_family_pre) | set(summary.by_family_live))
    for family in families:
        fam_rows.append(
            {
                "family": family,
                "pre $": summary.by_family_pre.get(family, 0.0),
                "live $": summary.by_family_live.get(family, 0.0),
            }
        )
    if fam_rows:
        st.subheader("Pre vs live by market family")
        st.dataframe(fam_rows, use_container_width=True, hide_index=True)

    st.subheader("Every buy fill")
    _render_tape_fill_table(tape)
    st.caption(
        "Harvest = Layer A. Gap / Clock / Pair / Flip / Tail = Layer B explanations. "
        "Not KEEP tickets. Gap after you see it on this page is usually a worse price."
    )


def _render_simulation(swiss: list[SwissLot]) -> None:
    st.markdown(
        """
        The **$70k mark is not locked profit**. It is the current resale value of
        shares that still settle at **$1 or $0** when each match ends. This tab
        walks every full-time score from 0–0 to 7–7 and prices his **open** lots
        on that score. Matches are treated as independent, so book-level best
        is “every match hits its best score” and worst is “every match hits its
        worst score.”
        """
    )
    book = simulate_book(swiss)
    if not book.fixtures:
        st.warning("Could not parse enough full-time markets to simulate.")
        return

    u_cost = sum(item.unmodeled_cost for item in book.fixtures)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open cost (settles)", f"${book.open_cost:,.0f}")
    c2.metric("Current mark (not locked)", f"${book.mark_usd:,.0f}")
    c3.metric("Best case (all matches)", f"${book.modeled_best:,.0f}", delta="FT only")
    c4.metric("Worst case (all matches)", f"${book.modeled_worst:,.0f}", delta="FT only")

    st.caption(
        f"Half-time / first-scorer lots are **not** in those two numbers "
        f"(${u_cost:,.0f} cost). If they all lose, worst becomes "
        f"**${book.conservative_worst:,.0f}**. If they all won (impossible as a set), "
        f"best would be at most **${book.optimistic_best:,.0f}**. "
        f"{book.skipped} fixtures could not be parsed."
    )

    chart_rows = [
        {
            "match": item.title.replace(" FC", "").replace(" CF", "")[:32],
            "Best P&L $": item.best.profit,
            "Worst P&L $": item.worst.profit,
        }
        for item in book.fixtures
    ]
    st.subheader("Best vs worst P&L by match")
    try:
        import pandas as pd

        st.bar_chart(
            pd.DataFrame(chart_rows).set_index("match")[["Best P&L $", "Worst P&L $"]],
            height=360,
        )
    except ImportError:
        st.dataframe(chart_rows, use_container_width=True, hide_index=True)

    summary = [
        {
            "match": item.title,
            "league": item.league,
            "open cost $": round(item.open_cost, 0),
            "mark $": round(item.mark_usd, 0),
            "best score": scoreline(item, item.best.home_goals, item.best.away_goals),
            "best P&L $": item.best.profit,
            "worst score": scoreline(item, item.worst.home_goals, item.worst.away_goals),
            "worst P&L $": item.worst.profit,
            "unmodeled $": item.unmodeled_cost,
        }
        for item in book.fixtures
    ]
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.subheader("Each match")
    for item in book.fixtures:
        best, worst = item.best, item.worst
        header = (
            f"{item.league.upper()} · {item.title} · "
            f"best {scoreline(item, best.home_goals, best.away_goals)} ${best.profit:,.0f} · "
            f"worst {scoreline(item, worst.home_goals, worst.away_goals)} ${worst.profit:,.0f}"
        )
        with st.expander(header):
            k1, k2, k3 = st.columns(3)
            k1.metric("Best", f"${best.profit:,.0f}", scoreline(item, best.home_goals, best.away_goals))
            k2.metric("Worst", f"${worst.profit:,.0f}", scoreline(item, worst.home_goals, worst.away_goals))
            k3.metric("Mark now", f"${item.mark_usd:,.0f}")

            named = []
            for hg, ag, label in KEY_SCORES:
                path = item.at(hg, ag)
                if path is None:
                    continue
                named.append(
                    {
                        "scenario": f"{label}: {scoreline(item, hg, ag)}",
                        "payout $": path.payout,
                        "cost $": path.cost,
                        "P&L $": path.profit,
                        "lots win": path.winners,
                        "lots lose": path.losers,
                    }
                )
            st.markdown("**Named scores** (home is the left team)")
            st.dataframe(named, use_container_width=True, hide_index=True)

            ranked = sorted(item.paths, key=lambda row: row.profit, reverse=True)
            ends = [
                {
                    "score": scoreline(item, row.home_goals, row.away_goals),
                    "P&L $": row.profit,
                    "payout $": row.payout,
                }
                for row in [*ranked[:6], *ranked[-6:][::-1]]
            ]
            st.markdown("**Six best and six worst scorelines** (0–7 goals each side)")
            st.dataframe(ends, use_container_width=True, hide_index=True)

            st.markdown(f"**What drives the worst score** ({scoreline(item, worst.home_goals, worst.away_goals)})")
            drivers = lot_results(item, worst.home_goals, worst.away_goals)[:8]
            st.dataframe(
                [
                    {
                        "result": "WIN" if won else "LOSE",
                        "P&L $": pnl,
                        "shares": round(item_lot.lot.shares_now or item_lot.lot.shares_bought, 1),
                        "avg ¢": round(item_lot.lot.avg_cents, 1),
                        "market": item_lot.lot.title,
                        "side": item_lot.lot.outcome,
                    }
                    for item_lot, won, pnl in drivers
                ],
                use_container_width=True,
                hide_index=True,
            )
            if item.unmodeled:
                st.caption(
                    f"{len(item.unmodeled)} lots not in the FT grid "
                    f"(half-time / first scorer / unparsed), cost ${item.unmodeled_cost:,.0f}."
                )


with tape_tab:
    st.markdown(
        "His **buy tape**, split at kickoff. This is Layer B. "
        "It is slower than Scan — one match at a time, 10–40 seconds."
    )
    choices = _tape_choices(reviewed, swiss)
    if not choices:
        st.info("Scan first so there are fixtures to pick.")
    else:
        labels = {}
        for slug in choices:
            his = lots_for_parent(swiss, slug)
            title = next((lot.title.split(":")[0] for lot in his if " vs" in lot.title.lower()), slug)
            labels[f"{title} · {slug}"] = slug
        picked_label = st.selectbox("Match", list(labels.keys()))
        picked = labels[picked_label]
        if st.button("Load buy tape", type="primary"):
            try:
                with st.spinner(f"Loading fills for {picked}…"):
                    st.session_state["tape"] = load_tape(picked)
                    st.session_state["tape_slug"] = picked
            except Exception as exc:
                st.session_state.pop("tape", None)
                st.error(str(exc))
        tape = st.session_state.get("tape")
        if tape is not None and st.session_state.get("tape_slug") == picked:
            _render_tape(tape)
        elif tape is not None:
            st.caption(f"Last loaded: {st.session_state.get('tape_slug')}. Click Load for this match.")


with sim_tab:
    if not swiss:
        st.info("Load swisstony’s book first (Scan live boards).")
    else:
        _render_simulation(swiss)


with book_tab:
    if not swiss:
        st.info("No swisstony lots loaded.")
    else:
        st.markdown(
            f"All **{len(swiss)}** open lots we can map to a football slug. "
            f"Bought **{swiss_shares:,.0f} shares** for **${swiss_usd:,.0f}**."
        )
        table = [
            {
                "league": lot.league,
                "match": lot.parent,
                "market": lot.title,
                "side": lot.outcome,
                "bought shares": round(lot.shares_bought, 2),
                "avg ¢": round(lot.avg_cents, 1),
                "cost $": round(lot.cost_usd, 2),
                "open shares": round(lot.shares_now, 2),
                "uPnL $": round(lot.u_pnl, 2),
            }
            for lot in swiss
        ]
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.link_button("Open swisstony on Polymarket", PROFILE)
