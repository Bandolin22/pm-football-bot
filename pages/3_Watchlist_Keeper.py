from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import datetime, timezone

import streamlit as st

from pm_football_bot.config import hydrate_env, load_settings
from pm_football_bot.dotenv_store import delete_dotenv_keys, mask_secret, upsert_dotenv
from pm_football_bot.execution import LiveTradingDisabled, env_funder, env_pk, reset_live_client, resolve_funder
from pm_football_bot.keeper import (
    KeeperScan,
    PlacedOrder,
    collect_keeper,
    load_keeper_config,
    load_placed,
    load_placed_book,
    open_tickets,
    place_keeper_book,
    refresh_placed_orders,
    ticket_key,
)
from pm_football_bot.models import Ticket

st.set_page_config(page_title="Watchlist keeper", layout="wide")

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
        border-left: 4px solid #1f8a4c;
      }
      .chip {
        display: inline-block;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0.02em;
        padding: 2px 8px;
        border-radius: 999px;
        margin-right: 6px;
        background: #e8f6ee;
        color: #17663a;
      }
      .chip-work { background: #fff4e5; color: #9a5b00; }
      .chip-part { background: #fde8d8; color: #c2410c; }
      .chip-fill { background: #e8eefc; color: #1d4ed8; }
      .chip-post { background: #f2f4f7; color: #475467; }
      .ticket-work { border-left-color: #d97706; }
      .ticket-fill { border-left-color: #2563eb; }
      .muted { color: #667085; font-size: 14px; }
      .price { font-variant-numeric: tabular-nums; }
    </style>
    """,
    unsafe_allow_html=True,
)

RULE_LABEL = {
    "keeper_fade_dog": "Dog does not win",
    "keeper_over_0_5": "Someone scores (cheapest Over 0.5)",
    "keeper_over_1_5": "Over 1.5 (0.5 books were 98c+)",
    "keeper_under_5_5": "Under 6 goals (Under 5.5)",
    "keeper_exact_else_no": "Exact score 0-0 to 3-3 (Any Other No)",
    "keeper_corners_7_5": "Over 7.5 corners (~75%)",
}


def _kickoff(ticket: Ticket) -> str:
    ko = ticket.kickoff
    if ko is None:
        return "Kickoff unknown"
    return ko.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")


def _kickoff_day(ticket: Ticket) -> str:
    ko = ticket.kickoff
    if ko is None:
        return "unknown"
    return ko.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _day_label(day: str) -> str:
    if day == "unknown":
        return "Kickoff unknown"
    parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return parsed.strftime("%A %d %b %Y")


def _poly_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"


def _profit(ticket: Ticket) -> float:
    return round(ticket.shares - ticket.cost_usd, 2)


def _group(tickets: list[Ticket]) -> list[tuple[str, list[Ticket]]]:
    buckets: dict[str, list[Ticket]] = defaultdict(list)
    order: list[str] = []
    for ticket in tickets:
        key = ticket.slug or ticket.fixture
        if key not in buckets:
            order.append(key)
        buckets[key].append(ticket)
    return [(key, buckets[key]) for key in order]


def _group_by_day(tickets: list[Ticket]) -> list[tuple[str, list[tuple[str, list[Ticket]]]]]:
    buckets: dict[str, list[Ticket]] = defaultdict(list)
    order: list[str] = []
    for ticket in tickets:
        day = _kickoff_day(ticket)
        if day not in buckets:
            order.append(day)
        buckets[day].append(ticket)
    return [(day, _group(buckets[day])) for day in order]


def render_ticket(ticket: Ticket) -> None:
    label = RULE_LABEL.get(ticket.rule_id, ticket.rule_id)
    spread = f"{ticket.spread:.3f}" if ticket.spread is not None else "—"
    st.markdown(
        f"""
        <div class="ticket">
          <span class="chip">TO BUY</span>
          <strong>{label}</strong>
          <span class="muted"> · buy {ticket.outcome}</span>
          <div style="margin-top:8px" class="price">
            <strong>{ticket.shares:.1f} shares</strong> at {ticket.price * 100:.1f}¢
            · pay ${ticket.cost_usd:.2f}
            · if it hits, back ~${ticket.shares:.2f} (about +${_profit(ticket):.2f})
          </div>
          <div class="muted" style="margin-top:6px">{ticket.meaning}</div>
          <div class="muted">{ticket.reason}</div>
          <div class="muted">Spread {spread} · {ticket.question}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _order_chip(order: PlacedOrder) -> tuple[str, str]:
    if order.filled:
        return "chip chip-fill", "FILLED"
    if order.status == "partial":
        return "chip chip-part", "PARTIAL"
    if order.status in {"live", "posted", "delayed"}:
        return "chip chip-work", "WORKING"
    return "chip chip-post", "POSTED"


def _order_kickoff(order: PlacedOrder) -> str:
    if not order.kickoff:
        return "Kickoff unknown"
    try:
        stamp = datetime.fromisoformat(order.kickoff.replace("Z", "+00:00"))
    except ValueError:
        return order.kickoff
    return stamp.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")


def _order_day(order: PlacedOrder) -> str:
    if not order.kickoff:
        return "unknown"
    try:
        stamp = datetime.fromisoformat(order.kickoff.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d")


def render_order(order: PlacedOrder) -> None:
    label = RULE_LABEL.get(order.rule, order.rule or order.key)
    chip_class, chip_text = _order_chip(order)
    box = "ticket ticket-fill" if order.filled else "ticket ticket-work"
    matched = order.size_matched
    size = order.shares
    fill_line = ""
    if size > 0:
        fill_line = f"{matched:.1f} / {size:.1f} shares filled"
        if order.filled and matched <= 0:
            fill_line = f"{size:.1f} shares filled"
    elif order.filled:
        fill_line = "Filled on Polymarket"
    else:
        fill_line = "Posted — waiting for a fill"
    price = f"{order.price * 100:.1f}¢" if order.price else "—"
    st.markdown(
        f"""
        <div class="{box}">
          <span class="{chip_class}">{chip_text}</span>
          <strong>{label}</strong>
          <span class="muted"> · {order.outcome or "buy"}</span>
          <div style="margin-top:8px" class="price">
            <strong>{fill_line}</strong>
            · limit {price}
            · {order.fixture or order.key}
          </div>
          <div class="muted" style="margin-top:6px">{order.meaning or order.question}</div>
          <div class="muted">{_order_kickoff(order)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _apply_ui_pk() -> None:
    typed = (st.session_state.get("keeper_pk_draft") or "").strip()
    if typed:
        os.environ["PK"] = typed
    funder = (st.session_state.get("keeper_funder_draft") or "").strip()
    if funder:
        os.environ["FUNDER"] = funder


def _save_pk() -> None:
    typed = (st.session_state.get("keeper_pk_draft") or "").strip()
    pk = typed or env_pk()
    if not pk:
        st.session_state["keeper_pk_error"] = "Paste a PK first."
        return
    payload = {"PK": pk, "SIGNATURE_TYPE": "3"}
    typed_funder = (st.session_state.get("keeper_funder_draft") or "").strip()
    if typed_funder:
        payload["FUNDER"] = typed_funder
    upsert_dotenv(payload)
    if not env_funder():
        try:
            resolve_funder(pk, persist=True)
        except LiveTradingDisabled:
            pass
    reset_live_client()
    st.session_state["keeper_pk_edit"] = False
    st.session_state.pop("keeper_pk_error", None)


def _delete_pk() -> None:
    delete_dotenv_keys(["PK", "FUNDER"])
    reset_live_client()
    st.session_state["keeper_pk_edit"] = True
    st.session_state.pop("keeper_pk_error", None)


def _render_pk_panel() -> None:
    saved = bool(env_pk())
    if "keeper_pk_edit" not in st.session_state:
        st.session_state["keeper_pk_edit"] = not saved
    err = st.session_state.get("keeper_pk_error")
    if err:
        st.error(err)
    if saved and not st.session_state["keeper_pk_edit"]:
        st.success(f"PK saved on this machine · {mask_secret(env_pk())}")
        funder = env_funder()
        if funder:
            st.caption(f"Deposit wallet {funder}")
        else:
            st.caption("Deposit wallet is looked up on the first live order if you leave it blank.")
        edit_col, delete_col = st.columns(2)
        if edit_col.button("Edit", use_container_width=True):
            st.session_state["keeper_pk_edit"] = True
            st.rerun()
        if delete_col.button("Delete", use_container_width=True):
            _delete_pk()
            st.rerun()
        return
    st.text_input(
        "Private key (PK)",
        type="password",
        key="keeper_pk_draft",
        placeholder="0x…  (leave blank when editing to keep the saved key)",
        help="Polygon wallet private key. Saved to .env on this machine (gitignored, not committed).",
    )
    if "keeper_funder_draft" not in st.session_state:
        st.session_state["keeper_funder_draft"] = env_funder()
    st.text_input(
        "Deposit wallet (optional)",
        key="keeper_funder_draft",
        placeholder="0x… from polymarket.com profile if auto-detect fails",
        help="Venue rejects EOA makers. Leave blank to look up the deposit wallet from the PK.",
    )
    save_col, cancel_col = st.columns(2)
    if save_col.button("Save PK", type="primary", use_container_width=True):
        _save_pk()
        if not st.session_state.get("keeper_pk_error"):
            st.rerun()
    if saved and cancel_col.button("Cancel", use_container_width=True):
        st.session_state["keeper_pk_edit"] = False
        st.session_state.pop("keeper_pk_error", None)
        st.rerun()


def _sync_orders(result: KeeperScan | None) -> dict[str, PlacedOrder]:
    tickets = result.tickets if result is not None else []
    settings = result.settings if result is not None else load_settings()
    try:
        book = refresh_placed_orders(settings, tickets)
    except Exception:
        book = load_placed_book()
    st.session_state["keeper_orders"] = book
    return book


def _orders() -> dict[str, PlacedOrder]:
    cached = st.session_state.get("keeper_orders")
    if isinstance(cached, dict):
        return cached
    book = load_placed_book()
    st.session_state["keeper_orders"] = book
    return book


def _group_orders(orders: list[PlacedOrder]) -> list[tuple[str, list[PlacedOrder]]]:
    buckets: dict[str, list[PlacedOrder]] = defaultdict(list)
    order: list[str] = []
    for rec in orders:
        key = rec.slug or rec.fixture or rec.key
        if key not in buckets:
            order.append(key)
        buckets[key].append(rec)
    return [(key, buckets[key]) for key in order]


def _group_orders_by_day(orders: list[PlacedOrder]) -> list[tuple[str, list[tuple[str, list[PlacedOrder]]]]]:
    buckets: dict[str, list[PlacedOrder]] = defaultdict(list)
    days: list[str] = []
    for rec in orders:
        day = _order_day(rec)
        if day not in buckets:
            days.append(day)
        buckets[day].append(rec)
    return [(day, _group_orders(buckets[day])) for day in days]


def _render_order_section(title: str, orders: list[PlacedOrder], *, empty: str) -> None:
    st.subheader(title)
    if not orders:
        st.caption(empty)
        return
    for day, matches in _group_orders_by_day(orders):
        day_rows = [row for _key, recs in matches for row in recs]
        with st.container(border=True):
            st.markdown(f"**{_day_label(day)}** · {len(day_rows)} order(s)")
            for _key, recs in matches:
                first = recs[0]
                league = league_names.get(first.league, first.league) if first.league else ""
                header = " · ".join(part for part in (league, first.fixture or first.key, _order_kickoff(first)) if part)
                with st.expander(header, expanded=True):
                    if first.slug:
                        st.link_button("Open on Polymarket", _poly_url(first.slug))
                    for rec in recs:
                        render_order(rec)


def _match_key(ticket: Ticket) -> str:
    return ticket.slug or ticket.fixture


def _open_book(tickets: list[Ticket]) -> list[Ticket]:
    return open_tickets(tickets, load_placed())


def _share_size() -> float:
    raw = st.session_state.get("keeper_shares")
    try:
        shares = float(raw)
    except (TypeError, ValueError):
        shares = 5.0
    return max(1.0, shares)


def _sized(tickets: list[Ticket], shares: float | None = None) -> list[Ticket]:
    size = float(shares if shares is not None else _share_size())
    sized: list[Ticket] = []
    for ticket in tickets:
        sized.append(replace(ticket, shares=size, cost_usd=round(size * ticket.price, 2)))
    return sized


def _append_log(lines: list[str]) -> None:
    log = list(st.session_state.get("keeper_log") or [])
    log.extend(lines)
    st.session_state["keeper_log"] = log[-80:]


def _record_place(out: dict, extra: list[str] | None = None) -> None:
    st.session_state["keeper_place"] = out
    lines = list(extra or [])
    lines.extend(f"placed {row['rule']} {row['fixture']}" for row in out.get("placed") or [])
    lines.extend(f"skip {key}" for key in out.get("skipped") or [])
    lines.extend(f"error {line}" for line in out.get("errors") or [])
    if out.get("fatal"):
        lines.append(f"fatal {out['fatal']}")
    _append_log(lines)


st.title("Watchlist keeper")
st.caption(
    "Watchlist bot: dog No, full-match Over 0.5, Under 5.5. "
    "Matches are grouped by UTC kickoff date. **Buy** a line, a match, or a whole date. "
    "Share size is set above the scan. Unsent tickets stay on the list; posted GTCs move to "
    "**Working** until they fill, then **Filled**. "
    "**Send live orders** posts the whole open list. Save your wallet **PK** once — it stays on this machine. "
    "**Auto-run** repeats the scan on a timer; tick the live box under it to buy each cycle. "
    "KEEP harvest on the home page stays dry-run."
)

hydrate_env()
settings = load_settings()
if "keeper_shares" not in st.session_state:
    st.session_state["keeper_shares"] = float(load_keeper_config().shares)
league_names = {row.key: row.name for row in settings.leagues}
all_keys = [row.key for row in settings.leagues]
picked = st.multiselect(
    "Leagues",
    options=all_keys,
    default=all_keys,
    format_func=lambda key: league_names.get(key, key),
)
ctrl1, ctrl2 = st.columns(2)
with ctrl1:
    horizon = st.slider("Lookahead (days)", min_value=1, max_value=14, value=7)
with ctrl2:
    st.number_input(
        "Shares per ticket",
        min_value=1.0,
        max_value=500.0,
        step=1.0,
        key="keeper_shares",
        help="Size for each live buy. Venue minimum is usually 5.",
    )
st.session_state["keeper_leagues"] = picked
st.session_state["keeper_horizon"] = horizon

_render_pk_panel()
_apply_ui_pk()
if not env_pk():
    st.warning("Save a PK to place live orders. Also `pip install -e .[live]`.")

with st.expander("How to read this", expanded=False):
    st.markdown(
        """
        Scan is always a shopping list. Tickets sit in **UTC date blocks**.
        Buy one line, all remaining tickets on a match, or **every open ticket
        on that date**. Share size is the number above Scan. Posted GTC bids
        leave the shopping list and show under **Working** until Polymarket
        fills them, then **Filled**. **Send live orders** posts the remaining
        shopping list. **Auto-run** repeats the cycle (working and filled
        tickets are skipped). Live orders use the deposit wallet (not the EOA).
        Save the PK once; Edit / Delete to change it.
        1X2 fades use football-data.co.uk form (API is fallback).
        Two watchlist clubs skip dog No and Under 5.5. Corners are not bought.
        """
    )

b1, b2, b3 = st.columns(3)
with b1:
    scan = st.button("Scan watchlist keeper", type="primary", use_container_width=True)
with b2:
    send = st.button("Send live orders", type="primary", use_container_width=True)
with b3:
    refresh = st.button("Refresh fills", use_container_width=True)

auto = st.toggle(
    "Auto-run keeper",
    key="keeper_auto",
    help=f"Rescan every {settings.poll_seconds}s while this page is open.",
)
live_ok = st.checkbox(
    "On auto-run, also place live GTC bids",
    key="keeper_live",
    help="Only used by Auto-run. Send live orders always posts when you click it.",
)


def _do_scan() -> None:
    if not picked:
        st.warning("Pick at least one league.")
        return
    progress = st.status("Scanning watchlist markets and form…", expanded=True)

    def _note(msg: str) -> None:
        progress.write(msg)

    try:
        st.session_state["keeper"] = collect_keeper(
            live=False,
            on_progress=_note,
            league_keys=set(picked),
            horizon_days=horizon,
        )
        progress.update(label="Scan finished", state="complete")
        _sync_orders(st.session_state.get("keeper"))
    except Exception as exc:
        progress.update(label="Scan failed", state="error")
        st.error(str(exc))


def _do_place() -> None:
    _apply_ui_pk()
    result: KeeperScan | None = st.session_state.get("keeper")
    if result is None:
        st.warning("Scan first so there are tickets to send.")
        return
    open_list = _open_book(result.tickets)
    if not open_list:
        st.warning("Nothing left to send — remaining tickets are working or already filled.")
        return
    progress = st.status("Sending live GTC bids…", expanded=True)

    def _note(msg: str) -> None:
        progress.write(msg)

    out = place_keeper_book(_sized(open_list), result.settings, live=True, on_progress=_note)
    _record_place(out)
    _sync_orders(result)
    if out["fatal"]:
        progress.update(label="Live trading disabled", state="error")
        st.error(str(out["fatal"]))
    elif out["errors"]:
        progress.update(label="Some orders failed", state="error")
    else:
        progress.update(label="Orders posted", state="complete")
        st.rerun()


def _do_place_tickets(rows: list[Ticket], settings, *, label: str) -> None:
    _apply_ui_pk()
    if not rows:
        st.warning("No tickets to send.")
        return
    shares = _share_size()
    progress = st.status(f"Sending {shares:g}-share orders · {label}…", expanded=True)

    def _note(msg: str) -> None:
        progress.write(msg)

    out = place_keeper_book(_sized(rows, shares), settings, live=True, on_progress=_note)
    _record_place(out, extra=[f"{shares:g}sh {label}"])
    _sync_orders(st.session_state.get("keeper"))
    if out["fatal"]:
        progress.update(label="Live trading disabled", state="error")
        st.error(str(out["fatal"]))
        return
    if out["errors"] and not out["placed"] and not out["skipped"]:
        progress.update(label="Order failed", state="error")
        return
    progress.update(label=f"{shares:g}-share orders posted", state="complete")
    st.rerun()


if scan:
    _do_scan()

if send:
    _do_place()

if refresh:
    _apply_ui_pk()
    _sync_orders(st.session_state.get("keeper"))
    st.rerun()

if auto:

    @st.fragment(run_every=settings.poll_seconds)
    def _auto_cycle() -> None:
        if not st.session_state.get("keeper_auto"):
            return
        leagues = list(st.session_state.get("keeper_leagues") or [])
        days = int(st.session_state.get("keeper_horizon") or 7)
        if not leagues:
            st.caption("Auto-run: pick at least one league.")
            return
        st.caption(f"Auto-run every {settings.poll_seconds}s · live={bool(st.session_state.get('keeper_live'))}")
        _apply_ui_pk()
        try:
            scanned = collect_keeper(live=False, league_keys=set(leagues), horizon_days=days)
            st.session_state["keeper"] = scanned
            _append_log([f"auto-scan {scanned.watched} watchlist · {len(scanned.tickets)} tickets"])
        except Exception as exc:
            _append_log([f"auto-scan failed: {exc}"])
            st.error(str(exc))
            return
        if st.session_state.get("keeper_live") and scanned.tickets:
            open_list = _sized(_open_book(scanned.tickets))
            out = place_keeper_book(open_list, scanned.settings, live=True)
            st.session_state["keeper_place"] = out
            _append_log(
                [f"auto-place {len(out['placed'])} new · skip {len(out['skipped'])} · err {len(out['errors'])}"]
            )
            if out["fatal"]:
                st.error(str(out["fatal"]))
        _sync_orders(st.session_state.get("keeper"))

    _auto_cycle()

result: KeeperScan | None = st.session_state.get("keeper")
book = _orders()
working = [row for row in book.values() if row.working]
filled = [row for row in book.values() if row.filled]
if result is None and not working and not filled:
    st.info("Press **Scan watchlist keeper** or turn on **Auto-run**.")
    st.stop()

already = load_placed()
shares = _share_size()
booked = _sized(_open_book(result.tickets), shares) if result is not None else []
keep_usd = round(sum(t.cost_usd for t in booked), 2)
profit = round(sum(_profit(t) for t in booked), 2)
ticketed = {t.fixture for t in result.tickets} if result is not None else set()
skipped = [title for title in result.fixtures if title not in ticketed] if result is not None else []
mode = "LIVE posted" if st.session_state.get("keeper_place") else "DRY-RUN scan"

m1, m2, m3, m4 = st.columns(4)
m1.metric("To buy", f"{len(booked)}")
m2.metric("Working GTC", f"{len(working)}")
m3.metric("Filled", f"{len(filled)}")
m4.metric("If unsent hit", f"+${profit:.2f}" if booked else "—")

st.caption(
    f"{mode} · {shares:g} shares · "
    f"poll {settings.poll_seconds}s"
    + (f" · {result.as_of.strftime('%Y-%m-%d %H:%M UTC')}" if result is not None else "")
)

place_out = st.session_state.get("keeper_place")
if place_out:
    if place_out.get("fatal"):
        st.error(place_out["fatal"])
    else:
        posted = place_out.get("placed") or []
        filled_n = sum(1 for row in posted if str(row.get("status") or "") in {"filled", "matched"})
        st.success(
            f"Live book: {len(posted)} posted ({filled_n} filled immediately) · "
            f"{len(place_out.get('skipped') or [])} already on the book · "
            f"{len(place_out.get('errors') or [])} failed"
        )
        for row in posted:
            state = "filled" if str(row.get("status") or "") in {"filled", "matched"} else "working"
            st.caption(f"{state} {row['rule']} · {row['fixture']}")
        for line in place_out.get("errors") or []:
            st.warning(line)

st.header("To buy")
if result is None:
    st.caption("Scan first to build the shopping list.")
elif not booked:
    if working or filled:
        st.caption("Nothing left to send. Working and filled orders are below.")
    else:
        st.success("No keeper tickets in this window. Watchlist matches were scanned; rules did not fire.")
else:
    for day, matches in _group_by_day(booked):
        day_tickets = [ticket for _key, rows in matches for ticket in rows]
        day_usd = round(sum(ticket.cost_usd for ticket in day_tickets), 2)
        with st.container(border=True):
            head, action = st.columns([3, 2])
            with head:
                st.subheader(_day_label(day))
                st.caption(
                    f"{len(matches)} match(es) · {len(day_tickets)} ticket(s) · "
                    f"${day_usd:.2f} at {shares:g} shares"
                )
            with action:
                if st.button(
                    f"Buy all on this date · {len(day_tickets)}",
                    key=f"buy-day-{day}",
                    type="primary",
                    use_container_width=True,
                ):
                    _do_place_tickets(day_tickets, result.settings, label=_day_label(day))
            for key, rows in matches:
                first = rows[0]
                league = league_names.get(first.league, first.league)
                header = f"{league} · {first.fixture} · {len(rows)} tickets · {_kickoff(first)}"
                with st.expander(header, expanded=True):
                    dog_pct = f"{first.dog_yes * 100:.1f}¢" if first.dog_yes is not None else "—"
                    fav_pct = f"{first.favorite_yes * 100:.1f}¢" if first.favorite_yes is not None else "—"
                    st.markdown(
                        f"**{first.favorite_team or 'Favorite'}** to win {fav_pct} vs "
                        f"**{first.dog_team or 'Dog'}** {dog_pct}"
                    )
                    st.link_button("Open on Polymarket", _poly_url(first.slug))
                    for ticket in rows:
                        render_ticket(ticket)
                        label = RULE_LABEL.get(ticket.rule_id, ticket.rule_id)
                        if st.button(
                            f"Buy {shares:g} shares · {label}",
                            key=f"buy-ticket-{ticket_key(ticket)}",
                            type="primary",
                            use_container_width=True,
                        ):
                            _do_place_tickets([ticket], result.settings, label=label)
                    if len(rows) > 1 and st.button(
                        f"Buy all remaining · {len(rows)} ticket(s)",
                        key=f"buy-match-{key}",
                        use_container_width=True,
                    ):
                        _do_place_tickets(rows, result.settings, label=first.fixture)

_render_order_section(
    "Working GTC (placed, not filled)",
    working,
    empty="No resting orders. Posted bids show here until Polymarket matches them.",
)
_render_order_section(
    "Filled (purchased)",
    filled,
    empty="No filled keeper orders yet.",
)

if skipped:
    with st.expander(f"Scanned, no ticket · {len(skipped)}", expanded=not booked):
        st.caption(
            "Usually a two-watchlist clash, missing form/H2H, favorite at home for Under 5.5, "
            "or every Over 0.5 book at 98¢+."
        )
        for title in skipped:
            st.caption(title)

log = st.session_state.get("keeper_log") or []
if log:
    with st.expander("Keeper log", expanded=auto):
        for line in reversed(log[-30:]):
            st.caption(line)
