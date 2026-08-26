from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone

import streamlit as st
from html import escape

from pm_football_bot.board import WATCH_QUERIES, list_upcoming
from pm_football_bot.config import load_settings
from pm_football_bot.gamma import GammaClient

st.set_page_config(page_title="Upcoming board", layout="wide")

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px; padding-top: 1.5rem; }
      table.board { width: 100%; border-collapse: collapse; font-size: 15px; }
      table.board th, table.board td {
        padding: 8px 10px;
        border-bottom: 1px solid #ececec;
        text-align: left;
        vertical-align: middle;
      }
      table.board th { color: #667085; font-weight: 600; font-size: 13px; }
      table.board td.price { font-variant-numeric: tabular-nums; }
      table.board tr.watch { background: #fff4cc; }
      table.board tr.watch td:first-child { box-shadow: inset 4px 0 0 #e2a400; font-weight: 700; }
      .star { color: #b54708; margin-right: 6px; }
      a.poly { font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Upcoming board")
st.caption(
    "Next Polymarket 1X2 markets for EPL, LaLiga, Ligue 1, Serie A, Bundesliga, and UCL. "
    "Home / Draw / Away are live Yes mids. Gold rows are your watchlist clubs. "
    "Telegram pings those clubs 1 hour before kickoff via GitHub Actions "
    "(tokens live in GitHub secrets, not in a committed .env). "
    "UCL is listed here even though harvest keeps it off. This page never places orders."
)
st.caption("Watchlist: " + " · ".join(WATCH_QUERIES))

settings = load_settings()
league_names = {row.key: row.name for row in settings.leagues}
all_keys = [row.key for row in settings.leagues]
picked = st.multiselect(
    "Leagues",
    options=all_keys,
    default=all_keys,
    format_func=lambda key: league_names.get(key, key),
)
cols = st.columns([1, 3])
with cols[0]:
    reload = st.button("Refresh", type="primary", use_container_width=True)


@st.cache_data(ttl=120, show_spinner="Loading Polymarket events…")
def _load(keys: tuple[str, ...]) -> list[dict]:
    cfg = load_settings()
    client = GammaClient(cfg)
    matches = list_upcoming(
        client,
        cfg.leagues,
        per_league=None,
        league_keys=set(keys),
        include_disabled=True,
    )
    return [
        {
            "league": row.league,
            "league_name": row.league_name,
            "title": row.title,
            "kickoff": row.kickoff.isoformat() if row.kickoff else None,
            "home_pct": row.home_pct,
            "draw_pct": row.draw_pct,
            "away_pct": row.away_pct,
            "url": row.url,
            "watch": row.watch,
        }
        for row in matches
    ]


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _kickoff(raw: str | None) -> str:
    if not raw:
        return "Kickoff unknown"
    stamp = datetime.fromisoformat(raw)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")


def _table(rows: list[dict]) -> None:
    if not rows:
        st.caption("No upcoming moneyline events in this set.")
        return
    lines = [
        "<table class='board'><thead><tr>",
        "<th></th><th>League</th><th>Kickoff (UTC)</th><th>Match</th>",
        "<th>Home</th><th>Draw</th><th>Away</th><th></th>",
        "</tr></thead><tbody>",
    ]
    for row in rows:
        cls = "watch" if row.get("watch") else ""
        star = "<span class='star'>★</span>" if row.get("watch") else ""
        lines.append(
            "<tr class='"
            + cls
            + "'>"
            + f"<td>{star}</td>"
            + f"<td>{escape(row['league_name'])}</td>"
            + f"<td>{_kickoff(row['kickoff'])}</td>"
            + f"<td>{star}{escape(row['title'])}</td>"
            + f"<td class='price'>{_pct(row['home_pct'])}</td>"
            + f"<td class='price'>{_pct(row['draw_pct'])}</td>"
            + f"<td class='price'>{_pct(row['away_pct'])}</td>"
            + f"<td><a class='poly' href='{row['url']}' target='_blank' rel='noopener'>Open</a></td>"
            + "</tr>"
        )
    lines.append("</tbody></table>")
    st.markdown("\n".join(lines), unsafe_allow_html=True)


if not picked:
    st.info("Pick at least one league.")
    st.stop()

if reload:
    _load.clear()

try:
    records = _load(tuple(picked))
except Exception as exc:
    st.error(f"Could not load Polymarket events: {exc}")
    st.stop()

watched = [row for row in records if row.get("watch")]
watch_tab, all_tab, league_tab = st.tabs(
    [
        f"Watchlist · {len(watched)}",
        f"All by kickoff · {len(records)}",
        "By league",
    ]
)

with watch_tab:
    st.caption("Watchlist clubs only, soonest kickoff first.")
    _table(watched)

with all_tab:
    st.caption("Every loaded upcoming moneyline, sorted by start time. Gold rows are watchlist clubs.")
    _table(records)

with league_tab:
    for key in picked:
        chunk = [row for row in records if row["league"] == key]
        n_watch = sum(1 for row in chunk if row.get("watch"))
        label = f"{league_names.get(key, key)} · {len(chunk)} upcoming"
        if n_watch:
            label += f" · {n_watch} watch"
        with st.expander(label, expanded=(key == "epl")):
            _table(chunk)
