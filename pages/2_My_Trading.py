from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import streamlit as st

from pm_football_bot.account import (
    DEFAULT_USERNAME,
    FACTOR_LABEL,
    format_teams,
    load_account,
    profile_url,
    summarize_factors,
    summarize_teams,
    totals,
)

st.set_page_config(page_title="My trading", layout="wide")

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px; padding-top: 1.5rem; }
      .muted { color: #667085; font-size: 14px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("My trading")
st.caption(
    "Public Polymarket book for your profile — positions, fills, and PnL by harvest "
    "factor and by every club you traded, not only the watchlist. "
    "PnL is after taker fees. Read-only Data API. This page never places orders. "
    "First load can take about a minute; then it caches for three minutes."
)

cols = st.columns([2, 1, 1])
with cols[0]:
    username = st.text_input("Polymarket username", value=DEFAULT_USERNAME).strip().lstrip("@")
with cols[1]:
    soccer_only = st.checkbox("Soccer only", value=True)
with cols[2]:
    reload = st.button("Refresh", type="primary", use_container_width=True)

if not username:
    st.info("Enter a Polymarket username.")
    st.stop()

st.markdown(f"[Open @{username} on Polymarket]({profile_url(username)})")


@st.cache_data(ttl=180, show_spinner="Loading public Polymarket book…")
def _load(name: str):
    return load_account(name)


if reload:
    _load.clear()

try:
    book = _load(username)
except Exception as exc:
    st.error(str(exc))
    st.stop()

summary = totals(book.lots, soccer_only)
all_summary = totals(book.lots, soccer_only=False)
scope = "soccer" if soccer_only else "all markets"

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Positions value", f"${book.positions_value:,.2f}")
m2.metric(f"{scope} PnL", f"${summary['pnl']:,.2f}")
m3.metric("Realized", f"${summary['realized']:,.2f}")
m4.metric("Unrealized", f"${summary['unrealized']:,.2f}")
m5.metric("Taker fees", f"${summary['fees']:,.2f}")
m6.metric("Biggest lot", f"${summary['biggest_win']:,.2f}")
st.caption(
    f"{int(summary['lots'])} lots in this view · {int(summary['open_lots'])} still open · "
    f"cost ${summary['cost']:,.2f} · open mark ${summary['mark']:,.2f}. "
    f"Gross {scope} PnL ${summary['gross_pnl']:,.2f} before ${summary['fees']:,.2f} taker fees. "
    f"All-markets net ${all_summary['pnl']:,.2f} after ${all_summary['fees']:,.2f} fees. "
    "A fixture is counted for every named side, so team totals can exceed overall PnL. "
    "1X2 dog No is any *Will X win* **No** bought at 70¢ or higher. "
    "Sports taker fee is shares × 0.05 × price × (1 − price); makers pay 0."
)

factor_tab, team_tab, open_tab, closed_tab, tape_tab = st.tabs(
    ["PnL by factor", "PnL by team", "Open", "Closed", "Recent fills"]
)


def _money(rows: pd.DataFrame, cols_in: list[str]) -> pd.DataFrame:
    out = rows.copy()
    for col in cols_in:
        if col in out.columns:
            out[col] = out[col].map(lambda value: round(float(value), 2))
    return out


with factor_tab:
    st.caption(
        "Harvest buckets: 1X2 dog No / Over 0.5 / Under 5.5 / Exact score No / corners. "
        "Everything else (draw No, favorite Yes, BTC, transfers) sits in Other. "
        "PnL is after taker fees."
    )
    factor_rows = summarize_factors(book.lots, soccer_only)
    factor_df = _money(
        pd.DataFrame(
            [
                {
                    "Factor": FACTOR_LABEL[row.key],
                    "Lots": row.lots,
                    "Cost $": row.cost,
                    "Open value $": row.mark,
                    "Fees $": row.fees,
                    "Realized $": row.realized,
                    "Unrealized $": row.unrealized,
                    "PnL $": row.pnl,
                }
                for row in factor_rows
            ]
        ),
        ["Cost $", "Open value $", "Fees $", "Realized $", "Unrealized $", "PnL $"],
    )
    st.dataframe(factor_df, use_container_width=True, hide_index=True)

with team_tab:
    watch = st.checkbox("Watchlist clubs only", value=False)
    st.caption(
        "Every parsed soccer side, including weekday dogs not on the watchlist. "
        "Watchlist names use the Upcoming aliases (Roma, Napoli, Barca, …). "
        "PnL is after taker fees. A fixture counts toward both clubs."
    )
    team_rows = summarize_teams(book.lots, soccer_only=soccer_only, watchlist_only=watch)
    if not team_rows:
        st.info("No lots matched a team name in this view yet.")
    else:
        team_df = _money(
            pd.DataFrame(
                [
                    {
                        "Team": row.label,
                        "Lots": row.lots,
                        "Cost $": row.cost,
                        "Open value $": row.mark,
                        "Fees $": row.fees,
                        "Realized $": row.realized,
                        "Unrealized $": row.unrealized,
                        "PnL $": row.pnl,
                    }
                    for row in team_rows
                ]
            ),
            ["Cost $", "Open value $", "Fees $", "Realized $", "Unrealized $", "PnL $"],
        )
        st.dataframe(team_df, use_container_width=True, hide_index=True)

with open_tab:
    open_lots = [
        lot
        for lot in book.lots
        if lot.status == "open" and (lot.soccer if soccer_only else True)
    ]
    if not open_lots:
        st.info("No open lots in this view.")
    else:
        open_df = _money(
            pd.DataFrame(
                [
                    {
                        "Market": lot.title,
                        "Outcome": lot.outcome,
                        "Factor": FACTOR_LABEL.get(lot.factor, lot.factor),
                        "Teams": format_teams(lot),
                        "Shares": round(lot.shares, 2),
                        "Avg ¢": lot.cents,
                        "Cost $": lot.cost_usd,
                        "Fees $": lot.fee_usd,
                        "Now $": lot.mark_usd,
                        "uPnL $": lot.net_pnl,
                    }
                    for lot in open_lots
                ]
            ),
            ["Cost $", "Fees $", "Now $", "uPnL $"],
        )
        st.dataframe(open_df, use_container_width=True, hide_index=True)

with closed_tab:
    closed_lots = [
        lot
        for lot in book.lots
        if lot.status != "open" and (lot.soccer if soccer_only else True)
    ]
    closed_lots = sorted(closed_lots, key=lambda lot: lot.net_pnl)
    winners = list(reversed(closed_lots[-25:])) if closed_lots else []
    losers = closed_lots[:25]
    left, right = st.columns(2)
    with left:
        st.subheader("Worst closed")
        if not losers:
            st.caption("None yet.")
        else:
            st.dataframe(
                _money(
                    pd.DataFrame(
                        [
                            {
                                "Market": lot.title,
                                "Outcome": lot.outcome,
                                "Factor": FACTOR_LABEL.get(lot.factor, lot.factor),
                                "Teams": format_teams(lot),
                                "Fees $": lot.fee_usd,
                                "PnL $": lot.net_pnl,
                            }
                            for lot in losers
                        ]
                    ),
                    ["Fees $", "PnL $"],
                ),
                use_container_width=True,
                hide_index=True,
            )
    with right:
        st.subheader("Best closed")
        if not winners:
            st.caption("None yet.")
        else:
            st.dataframe(
                _money(
                    pd.DataFrame(
                        [
                            {
                                "Market": lot.title,
                                "Outcome": lot.outcome,
                                "Factor": FACTOR_LABEL.get(lot.factor, lot.factor),
                                "Teams": format_teams(lot),
                                "Fees $": lot.fee_usd,
                                "PnL $": lot.net_pnl,
                            }
                            for lot in winners
                        ]
                    ),
                    ["Fees $", "PnL $"],
                ),
                use_container_width=True,
                hide_index=True,
            )
    if closed_lots:
        st.subheader("All closed")
        closed_df = _money(
            pd.DataFrame(
                [
                    {
                        "When": lot.timestamp.strftime("%Y-%m-%d %H:%M") if lot.timestamp else "",
                        "Market": lot.title,
                        "Outcome": lot.outcome,
                        "Factor": FACTOR_LABEL.get(lot.factor, lot.factor),
                        "Teams": format_teams(lot),
                        "Shares": round(lot.shares, 2),
                        "Avg ¢": lot.cents,
                        "Cost $": lot.cost_usd,
                        "Fees $": lot.fee_usd,
                        "PnL $": lot.net_pnl,
                    }
                    for lot in sorted(
                        closed_lots,
                        key=lambda lot: lot.timestamp.timestamp() if lot.timestamp else 0,
                        reverse=True,
                    )
                ]
            ),
            ["Cost $", "Fees $", "PnL $"],
        )
        st.dataframe(closed_df, use_container_width=True, hide_index=True)

with tape_tab:
    fills = [row for row in book.fills if row.soccer] if soccer_only else book.fills
    st.caption(f"Latest {len(fills)} public fills (capped on load). BUY and SELL.")
    if not fills:
        st.info("No fills in this view.")
    else:
        fill_df = _money(
            pd.DataFrame(
                [
                    {
                        "When UTC": row.utc.strftime("%Y-%m-%d %H:%M"),
                        "Side": row.side,
                        "Market": row.title,
                        "Outcome": row.outcome,
                        "Factor": FACTOR_LABEL.get(row.factor, row.factor),
                        "Teams": ", ".join(row.teams),
                        "Shares": round(row.shares, 2),
                        "¢": round(row.price * 100, 1),
                        "$": row.usd,
                        "Fee $": row.fee_usd,
                    }
                    for row in fills
                ]
            ),
            ["$", "Fee $"],
        )
        st.dataframe(fill_df, use_container_width=True, hide_index=True)
