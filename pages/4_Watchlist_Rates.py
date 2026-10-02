from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from pm_football_bot.watch_rates import load_watch_rates

st.set_page_config(page_title="Watchlist rates", layout="wide")

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px; padding-top: 1.5rem; }
      .muted { color: #667085; font-size: 14px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Watchlist rates · home, away, and watchlist clashes")
st.caption(
    "Rolling ~one year of completed games up to today. 0–0, Over 5.5 (6+ goals), "
    "and Over 3–3 (a side scored 4+, so 4–0 counts here). Clubs are domestic league "
    "only. Nations use the public senior men’s results file. Clash = both sides on "
    "the watchlist. Refresh re-downloads; otherwise this page caches for 45 minutes. "
    "This page never places orders."
)

days = st.slider("Lookback (days)", min_value=180, max_value=400, value=365, step=5)
reload = st.button("Refresh rates", type="primary")


@st.cache_data(ttl=45 * 60, show_spinner="Loading one year of results…")
def _load(lookback: int) -> dict:
    report = load_watch_rates(days=lookback, now=datetime.now(timezone.utc))
    return report.as_dict()


if reload:
    _load.clear()

data = _load(days)
clash = data["clash"]
rest = data["rest"]
teams = data["teams"]
clubs = [row for row in teams if row["kind"] == "club"]
nations = [row for row in teams if row["kind"] == "nation"]

st.caption(
    f"Window {data['start']} → {data['end']} UTC · {clash['matches'] + rest['matches']} unique "
    f"watchlist fixtures · last loaded {data['as_of']}"
)
if data.get("errors"):
    st.caption("Skipped (missing or timed out): " + ", ".join(data["errors"][:12]))


def _grouped_chart(rows: list[dict], venue: str, height: int) -> alt.Chart:
    if not rows:
        return alt.Chart(pd.DataFrame({"team": [], "metric": [], "rate": []})).mark_bar()
    labels = [row["label"] for row in rows]
    records = []
    for row in rows:
        bucket = row[venue]
        records.append({"team": row["label"], "metric": "0–0", "rate": bucket["zero_rate"]})
        records.append({"team": row["label"], "metric": "Over 5.5", "rate": bucket["over55_rate"]})
        records.append({"team": row["label"], "metric": "Over 3–3", "rate": bucket["other_rate"]})
    frame = pd.DataFrame(records)
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            y=alt.Y("team:N", sort=labels, title=None),
            x=alt.X("rate:Q", title="Percent of games", scale=alt.Scale(domain=[0, 55])),
            color=alt.Color(
                "metric:N",
                scale=alt.Scale(
                    domain=["0–0", "Over 5.5", "Over 3–3"],
                    range=["#667085", "#d97706", "#b42318"],
                ),
                legend=alt.Legend(title=None, orient="top"),
            ),
            xOffset="metric:N",
            tooltip=["team", "metric", "rate"],
        )
        .properties(height=height)
    )


def _rate_table(rows: list[dict], venue: str) -> pd.DataFrame:
    out = []
    for row in rows:
        bucket = row[venue]
        out.append(
            {
                "Team": row["label"],
                "Games": bucket["matches"],
                "0–0": f"{bucket['zero']} · {bucket['zero_rate']}%",
                "Over 5.5": f"{bucket['over55']} · {bucket['over55_rate']}%",
                "Over 3–3": f"{bucket['other']} · {bucket['other_rate']}%",
            }
        )
    return pd.DataFrame(out)


st.info(
    f"Watchlist-vs-watchlist ({clash['matches']} games): 0–0 {clash['zero_rate']}% · "
    f"Over 5.5 {clash['over55_rate']}% · Over 3–3 {clash['other_rate']}%. "
    f"One watchlist side ({rest['matches']} games): 0–0 {rest['zero_rate']}% · "
    f"Over 5.5 {rest['over55_rate']}% · Over 3–3 {rest['other_rate']}%."
)

c1, c2, c3 = st.columns(3)
c1.metric("Clash 0–0", f"{clash['zero_rate']}%", f"{clash['zero']} / {clash['matches']}")
c2.metric("Clash Over 5.5", f"{clash['over55_rate']}%", f"{clash['over55']} / {clash['matches']}")
c3.metric("Clash Over 3–3", f"{clash['other_rate']}%", f"{clash['other']} / {clash['matches']}")

st.subheader("Clash vs rest of the board")
st.caption("Unique fixtures. Clash is two watchlist sides; rest is one watchlist side against anyone else.")
compare = pd.DataFrame(
    [
        {"sample": f"Watchlist clash ({clash['matches']})", "metric": "0–0", "rate": clash["zero_rate"]},
        {"sample": f"Watchlist clash ({clash['matches']})", "metric": "Over 5.5", "rate": clash["over55_rate"]},
        {"sample": f"Watchlist clash ({clash['matches']})", "metric": "Over 3–3", "rate": clash["other_rate"]},
        {"sample": f"One watchlist side ({rest['matches']})", "metric": "0–0", "rate": rest["zero_rate"]},
        {"sample": f"One watchlist side ({rest['matches']})", "metric": "Over 5.5", "rate": rest["over55_rate"]},
        {"sample": f"One watchlist side ({rest['matches']})", "metric": "Over 3–3", "rate": rest["other_rate"]},
    ]
)
st.altair_chart(
    alt.Chart(compare)
    .mark_bar()
    .encode(
        x=alt.X("metric:N", title=None),
        y=alt.Y("rate:Q", title="Percent", scale=alt.Scale(domain=[0, 20])),
        color=alt.Color("sample:N", legend=alt.Legend(orient="top")),
        xOffset="sample:N",
        tooltip=["sample", "metric", "rate"],
    )
    .properties(height=240),
    use_container_width=True,
)

st.subheader("Clubs at home")
st.caption("Percent of that club’s home league games. Same team order as the away chart (sorted by average Over 5.5).")
st.altair_chart(_grouped_chart(clubs, "home", 920), use_container_width=True)
st.dataframe(_rate_table(clubs, "home"), hide_index=True, use_container_width=True)

st.subheader("Clubs away")
st.caption("Same clubs, away league games only.")
st.altair_chart(_grouped_chart(clubs, "away", 920), use_container_width=True)
st.dataframe(_rate_table(clubs, "away"), hide_index=True, use_container_width=True)

st.subheader("Nations at home")
st.caption("Small samples. Treat the percentages as rough.")
st.altair_chart(_grouped_chart(nations, "home", 560), use_container_width=True)
st.dataframe(_rate_table(nations, "home"), hide_index=True, use_container_width=True)

st.subheader("Nations away")
st.caption("Same national teams, away games only.")
st.altair_chart(_grouped_chart(nations, "away", 560), use_container_width=True)
st.dataframe(_rate_table(nations, "away"), hide_index=True, use_container_width=True)

st.subheader("Per team in watchlist clashes")
st.caption("Games that side played against another watchlist team (home and away combined).")
clash_rows = [row for row in teams if row["clash"]["matches"] > 0]
st.dataframe(
    pd.DataFrame(
        [
            {
                "Team": row["label"],
                "Kind": row["kind"],
                "Clash games": row["clash"]["matches"],
                "0–0": f"{row['clash']['zero']} · {row['clash']['zero_rate']}%",
                "Over 5.5": f"{row['clash']['over55']} · {row['clash']['over55_rate']}%",
                "Over 3–3": f"{row['clash']['other']} · {row['clash']['other_rate']}%",
            }
            for row in sorted(clash_rows, key=lambda row: (-row["clash"]["matches"], row["label"]))
        ]
    ),
    hide_index=True,
    use_container_width=True,
)

with st.expander(f"Every watchlist-vs-watchlist result ({len(data['clash_games'])})", expanded=False):
    games = data["clash_games"]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Date": row["date"],
                    "Fixture": f"{row['home']} vs {row['away']}",
                    "Score": row["score"],
                    "Flag": "0–0" if row["zero"] else ("Over 5.5" if row["over55"] else ("Over 3–3" if row["other"] else "")),
                }
                for row in games
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )

st.caption("Sources: " + " · ".join(data["sources"][:8]) + ("…" if len(data["sources"]) > 8 else ""))
