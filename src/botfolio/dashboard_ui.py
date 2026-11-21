"""Paper execution controls and the live worker's news audit."""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from botfolio.config import watchlist as load_watchlist
from botfolio.data.calendar import get_calendar, ny_date
from botfolio.events.paper import configured

MAX_WATCHLIST = 500

ACTIONS = {"WAIT": "Wait — no trade suggested", "HOLD": "Hold — no new trade suggested",
           "BUY": "Buy hypothesis", "SELL": "Sell hypothesis"}


def news_age(events, now=None):
    if not events:
        return None
    return max(0, ((now or datetime.now(UTC)) - max(e.content_at for e in events)).total_seconds() / 3600)


def render_news(store):
    st.title("News & AI")
    st.write("Read the saved news and see how the model interpreted it. Broker orders appear in the Paper trading journal.")
    events = store.events()
    if not events:
        st.info("No news saved yet. The news worker populates this page as it receives events.")
        return
    tickers = sorted({e.ticker for e in events})
    ticker = st.selectbox("Company or fund", ["All"] + tickers)
    audit = store.audit()
    experiment = st.selectbox("AI assessment version", ["Latest saved assessment"] +
        sorted(audit.experiment.unique()), help="Different versions may use different models or prompts. They are separate experiments.")
    stats = store.decision_stats()
    if experiment != "Latest saved assessment":
        audit = audit[audit.experiment == experiment]
        stats = stats[stats.experiment == experiment]
    if not stats.empty:
        with st.expander("Decision statistics by experiment, model and news source"):
            st.dataframe(stats, hide_index=True)
    latest = audit.sort_values("completed_at").drop_duplicates("event_key", keep="last").set_index("event_key")
    shown = [e for e in sorted(events, key=lambda e: e.content_at, reverse=True) if ticker == "All" or e.ticker == ticker]
    age = news_age(shown)
    if age is not None and age > 24:
        st.warning("These are archived articles. The newest is more than a day old; do not treat these assessments as current signals.")
    st.caption(f"Showing up to 20 of {len(shown)} saved records. Times are UTC. One article may mention several tickers.")
    for event in shown[:20]:
        with st.container():
            st.divider()
            record = latest.loc[event.key] if event.key in latest.index else None
            decision = json.loads(record.payload) if record is not None else None
            st.subheader(f"{event.ticker} · {ACTIONS.get(decision['recommended_action'], 'Not assessed') if decision else 'Not assessed'}")
            st.caption(f"Published {event.published_at:%d %b %Y, %H:%M} UTC · Saved {event.received_at:%d %b %Y, %H:%M} UTC")
            st.text(event.text[:500] + ("…" if len(event.text) > 500 else ""))
            st.link_button("Read original source", event.source_url)
            if decision:
                st.markdown("**Why this assessment?**")
                st.text(decision["reasoning"])
                st.caption(f"Model: {record.model} · Assessment version: {record.experiment}")
                if decision["recommended_action"] == "WAIT":
                    st.info("Wait means the app is abstaining. It may lack evidence, have high uncertainty, or have no AI provider configured.")
                with st.expander("Evidence and limitations"):
                    for evidence in decision["key_evidence"]:
                        st.text(evidence)
                    for risk in decision["risk_factors"]:
                        st.text(risk)
            else:
                st.info("Saved for reading. There is no AI assessment for this event yet.")
    with st.expander("Advanced: saved AI requests and responses"):
        st.dataframe(audit, hide_index=True)


def render_paper(cfg, storage):
    st.title("Control room")
    st.caption("Orders go to Alpaca's paper broker and use simulated money. Real-money trading is not supported.")
    watchlist = load_watchlist(storage, cfg)
    now = datetime.now(UTC)
    calendar = get_calendar("NASDAQ")
    session = calendar.session_for(now)
    upcoming = next((s for s in calendar.sessions_between(ny_date(now), ny_date(now)+timedelta(days=10))
                     if s.open > now), None)
    state = storage.get("event-execution-state") or {}
    healthy = bool(state and 0 <= (now-datetime.fromisoformat(state["at"])).total_seconds() <= 20
                   and state.get("phase") != "error")
    armed = bool(storage.get("event-paper-armed"))
    kill = Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL"))
    strategies, limits, status = cfg.strategies, cfg.limits(), storage.get("strategy-status") or {}
    a, b, c = st.columns(3)
    a.metric("Nasdaq regular session", "Open" if session else "Closed")
    b.metric("Execution monitor", "Recent check" if healthy else "Not ready")
    c.metric("New paper entries", "Enabled" if configured() and armed else "Paused")
    if session:
        st.caption(f"Session closes {session.close.astimezone(ZoneInfo('America/New_York')):%H:%M} Eastern. Entries stop five minutes before closing; time exits begin two minutes before closing.")
    elif upcoming:
        st.caption(f"Next regular open: {upcoming.open.astimezone(ZoneInfo('America/New_York')):%a %d %b, %H:%M} Eastern. Holidays, early closes and daylight saving are included.")
    data = storage.get("event-data-status") or {}
    latest = data.get("latest_content")
    if not latest or now-datetime.fromisoformat(latest) > timedelta(minutes=5):
        st.warning("No current news is available. Old articles cannot trigger news entries; momentum and ETF trend are unaffected.")
    if data.get("provider_failures"):
        st.error("A data provider failed its last check. Entries are blocked for that collection cycle.")
    if data.get("issuer_feed_failures"):
        st.warning("One or more issuer news feeds failed their last check.")
    if state.get("message"):
        st.error(state["message"])
    if not configured():
        st.info("Broker execution is disabled in this deployment. The app operator must enable paper execution and confirm a dedicated paper account before entries can be armed.")

    st.divider()
    entries, stop = st.columns([3, 2], gap="large")
    with entries:
        st.subheader("New entries")
        if configured() and armed:
            st.success("**Paper entries are ON.** The bot will place a paper trade when any strategy gives a valid signal "
                       "during market hours." + ("" if session else " Right now it is waiting because the market is closed."))
        else:
            st.info("**Paper entries are OFF.** The bot will not open new trades.")
        dedicated = st.checkbox("This paper account is used only by this bot.",
            help="A dedicated Alpaca paper account with no manual trading and no other bot.")
        ready = [("Paper execution switched on by the app operator", configured()),
                 ("Background worker checked in within the last 20 seconds", healthy),
                 ("Dedicated paper account confirmed (box above)", dedicated)]
        if not all(ok for _, ok in ready):
            st.markdown("**Enable** unlocks when nothing below is still needed:\n\n" +
                        "\n".join(f"- {'Done' if ok else '**Still needed**'}: {label}" for label, ok in ready))
        ai_ready = os.getenv("EVENT_AI_PROVIDER", "disabled") != "disabled" and bool(os.getenv("EVENT_AI_KEY"))
        if strategies.news.enabled and not ai_ready:
            st.caption("No AI news reader is set up, so the news strategy will not trade. "
                       "Momentum and ETF trend do not need one.")
        left, right = st.columns(2)
        if left.button("Enable paper entries", disabled=armed or not all(ok for _, ok in ready), type="primary"):
            storage.put("event-paper-armed", True)
            st.rerun()
        if right.button("Pause new entries", disabled=not armed):
            storage.put("event-paper-armed", False)
            st.rerun()
        st.caption("Pausing stops new trades only; positions already open are still managed and closed normally.")
    with stop:
        st.subheader("Emergency stop")
        st.caption("Pauses entries, cancels the bot's open orders and closes its paper positions during regular hours.")
        if st.button("Emergency stop paper trading"):
            storage.put("event-paper-armed", False)
            kill.parent.mkdir(parents=True, exist_ok=True)
            kill.touch()
            st.rerun()
        if kill.exists():
            st.warning("Emergency stop is active. Position closure requires a healthy worker, broker connection and an open market.")
            if st.button("Clear emergency stop; keep entries paused"):
                kill.unlink(missing_ok=True)
                storage.put("event-paper-armed", False)
                st.rerun()

    st.divider()
    st.subheader("Strategies")
    st.caption("Each strategy places real orders in the Alpaca Paper account. A symbol is held by one strategy at a time. "
               "All strategies share a 3% daily loss cutoff, the pause switch and the emergency stop. Stops can gap beyond the allowance.")
    st.dataframe(pd.DataFrame([
        dict(Strategy="News + AI", On=strategies.news.enabled, Trades="Fresh watchlist news with a directional AI call",
             Limits=f"{limits['news']['max_positions']} positions · {limits['news']['position_pct']:g}% of equity each · "
                    f"{limits['news']['risk_pct']:g}% risk", Latest=(storage.get("event-last-entry-check") or {}).get("result", "")),
        dict(Strategy="Intraday momentum", On=strategies.momentum.enabled,
             Trades="30-minute noise-band breakouts, long or short, flat by the close",
             Limits=f"{limits['momentum']['max_positions']} positions · {limits['momentum']['position_pct']:g}% of equity each · "
                    f"{limits['momentum']['risk_pct']:g}% risk", Latest=status.get("momentum", {}).get("note", "")),
        dict(Strategy="ETF trend", On=strategies.etf_trend.enabled,
             Trades=f"Holds watchlist ETFs above their {strategies.etf_trend.sma_days}-day average",
             Limits=f"{strategies.etf_trend.allocation_pct:g}% of equity across {len(cfg.etfs)} ETFs",
             Latest=status.get("etf_trend", {}).get("note", "")),
    ]), hide_index=True)

    st.divider()
    st.subheader("Watchlist")
    st.session_state.setdefault("event_watchlist_symbols", ", ".join(watchlist))
    with st.form("event_watchlist", border=False):
        value = st.text_area(f"Watchlist ({len(watchlist)} symbols)", key="event_watchlist_symbols", height=140,
            help=f"Enter up to {MAX_WATCHLIST} ticker symbols, separated by commas. Every strategy trades from this list; "
                 "the ETF trend strategy uses the ETFs in it.")
        if st.form_submit_button("Save watchlist"):
            symbols = list(dict.fromkeys(part.strip().upper() for part in value.split(",") if part.strip()))
            if (not symbols or len(symbols) > MAX_WATCHLIST
                    or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol) for symbol in symbols)):
                st.error(f"Enter 1–{MAX_WATCHLIST} valid ticker symbols separated by commas.")
            else:
                storage.put("watchlist", symbols)
                st.success(f"Watchlist saved with {len(symbols)} symbols. The worker will use it on its next cycle.")

    st.divider()
    st.subheader("Broker order journal")
    with storage.tx() as conn:
        rows = conn.execute("SELECT event_key,payload FROM event_orders").fetchall()
    if rows:
        journal = pd.DataFrame([dict(event=key, **{"strategy": "news", **json.loads(payload)}) for key, payload in rows])
        first = [c for c in ("opened", "strategy", "symbol", "side", "qty", "limit", "stop", "state", "broker_status",
                             "exit_requested") if c in journal.columns]
        if "opened" in journal.columns:
            journal = journal.sort_values("opened", ascending=False)
        st.dataframe(journal[first + [c for c in journal.columns if c not in first]], hide_index=True)
    else:
        st.info("No broker orders have been recorded. The system waits for a valid signal from any strategy.")
    check = storage.get("event-last-entry-check")
    if check:
        st.caption(f"Latest news entry check: {check.get('ticker', '')} {check.get('result', '')} ({check.get('at', '')})")
    st.caption("Use Refresh page to update status. A recent check is not an uptime guarantee. Paper trading does not establish live profitability.")
