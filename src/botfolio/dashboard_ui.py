"""Beginner research views and explicit controls for the separate paper executor."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from botfolio.analysis import ranking_table
from botfolio.config import load_credentials
from botfolio.data.calendar import get_calendar, ny_date
from botfolio.events.store import EventStore

log = logging.getLogger(__name__)
PAGES = ["Start here", "Results", "News & AI", "Paper trading", "Help", "Advanced research"]
PAGE_HINTS = ["Set up and try a demo", "Did the strategies make money?", "Company news and AI notes",
              "Turn the practice bot on or off", "Glossary and setup guides", "Detailed statistics for experts"]
ACTIONS = {"WAIT": "Wait — no trade suggested", "HOLD": "Hold — no new trade suggested",
           "BUY": "Buy hypothesis — research only", "SELL": "Sell hypothesis — research only"}
GROUPS = {"pattern": "Chart strategy", "inverse": "Opposite-direction control", "random": "Random trading",
          "benchmark": "Buy and hold"}
GROUP_COLORS = {"pattern": "#2a78d6", "inverse": "#eb6834", "random": "#8a8985", "benchmark": "#1baf7a"}
STRATEGY_NAMES = {"noise_mom": "Intraday noise-band momentum", "noise_qqq": "Noise-area momentum QQQ",
                  "noise_spy": "Noise-area momentum SPY", "random": "Random trading"}


def friendly(name):
    """Turn a roster name such as inv_noise_mom_1m into words a beginner can read."""
    prefix, base = ("Opposite of ", name[4:]) if name.startswith("inv_") else ("", name)
    if base.startswith("buy_hold_"):
        return f"Buy and hold {base[9:]}"
    if base.startswith("trend_"):
        return f"Trend-filtered hold {base[6:]}"
    m = re.fullmatch(r"(.+)_(\d+)m", base)
    core, tf = (m.group(1), f" · {m.group(2)}-min chart") if m else (base, "")
    return prefix + STRATEGY_NAMES.get(core, core.replace("_", " ")) + tf


def verdict_text(table):
    """One plain-language answer to "did the chart strategies work?" -> (streamlit box kind, markdown)."""
    pat = table[table.group == "pattern"]
    if pat.empty:
        return None
    best = pat.total_return_pct.idxmax()
    best_ret = pat.total_return_pct[best]
    compare = {label: table[table.group == group].total_return_pct.mean()
               for group, label in (("random", "Random trading"), ("benchmark", "Buy and hold"))}
    compare = {k: v for k, v in compare.items() if pd.notna(v)}
    lines = [f"**Best chart strategy:** {friendly(best)} — **{best_ret:+.2f}%** after trading costs."]
    if compare:
        lines.append(" · ".join(f"{k} averaged {v:+.2f}%" for k, v in compare.items()) + ".")
    edges = int((pat.verdict == "significant edge").sum()) if "verdict" in pat else 0
    if edges:
        kind, end = "success", (f"{edges} of {len(pat)} chart strategies passed the luck check. Worth re-testing on new "
                                "data — still not a promise of future profit.")
    elif best_ret > max(compare.values(), default=float("-inf")):
        kind, end = "info", ("The best one beat the comparisons, but none passed the luck check. When many strategies "
                             "are tried, one usually looks good by chance.")
    else:
        kind, end = "warning", "No chart strategy beat the simple comparisons in this test."
    return kind, "\n\n".join(lines + [f"**Bottom line:** {end}"])


def is_demo(run):
    notes = str(run.get("notes") or "").lower()
    return "synthetic" in notes or "demo" in notes


def run_label(run):
    kind = "Practice demo" if is_demo(run) else "Historical test" if run["mode"] == "backtest" else "Forward tracking"
    return f"{kind} · {run['start'] or 'date unavailable'} to {run['end'] or 'ongoing'} · {run['run_id']}"


def news_age(events, now=None):
    if not events:
        return None
    return max(0, ((now or datetime.now(UTC)) - max(e.content_at for e in events)).total_seconds() / 3600)


def collect_news(cfg, store):
    from botfolio.data.alpaca import AlpacaClient
    from botfolio.events.providers import ISSUER_FEEDS, AlpacaNews, IssuerNews
    from botfolio.safety import check_environment

    check_environment()
    client = AlpacaClient(load_credentials(), timeout=15, max_retries=2)
    now = datetime.now(UTC)
    watchlist = store.storage.get("event-watchlist") or cfg.symbols
    start = now - timedelta(days=1)
    events = AlpacaNews(client, watchlist).fetch(start, now)
    for ticker, url in ISSUER_FEEDS.items():
        if ticker in watchlist:
            try:
                events.extend(IssuerNews(ticker, url).fetch(start, now))
            except Exception:
                log.exception("issuer news collection failed", extra={"ticker": ticker})
    return sum(store.add(event) for event in events)


def practice_demo(cfg, storage):
    from botfolio.backtest import run_backtest
    from botfolio.data.synthetic import random_walk_bars

    # Fixed generated prices make the lesson reproducible; no broker client is created.
    start, end = date(2025, 1, 6), date(2025, 1, 10)
    risk = cfg.risk.model_copy(update=dict(starting_capital=10000., risk_per_trade_pct=.25,
        daily_loss_limit_pct=1., max_position_pct=25., max_gross_exposure_pct=100., max_open_positions=1))
    demo_cfg = cfg.model_copy(update={"risk": risk, "mirror_strategy": None})
    bars = random_walk_bars(get_calendar(), cfg.symbols, start, end, seed=cfg.seed)
    run_id, _ = run_backtest(demo_cfg, storage, None, start, end, run_id="demo-" + uuid4().hex[:10],
        bars_df=bars, monte_carlo=False, notes="DEMO: synthetic generated prices. For learning only; not evidence of an edge.")
    return run_id


def render_home(cfg, storage, store):
    st.title("Your trading research workspace")
    st.write("Learn how trading ideas are tested, follow company news, and review simulated results.")
    st.caption("Paper trading uses pretend money. A profitable-looking test does not promise future profits.")
    events = store.events()
    watchlist = storage.get("event-watchlist") or cfg.symbols
    credentials = load_credentials(required=False) is not None
    ai_ready = (os.getenv("EVENT_AI_PROVIDER", "disabled") != "disabled"
                and all(os.getenv(name) for name in ("EVENT_AI_KEY", "EVENT_AI_MODEL", "EVENT_AI_URL")))
    paper_enabled = os.getenv("EVENT_PAPER_ENABLED") == "true" and os.getenv("EVENT_EXCLUSIVE_ACCOUNT") == "true"
    killed = Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL")).exists()
    a, b, c = st.columns(3)
    a.metric("Market data (Alpaca)", "Checked this session" if st.session_state.get("paper_checked") else
             "Ready to check" if credentials else "Setup needed", help="Alpaca supplies prices and news.")
    b.metric("AI news reader", "Configured" if ai_ready else "Not set up",
             help="Optional. An AI model can read news and explain what it might mean.")
    c.metric("News articles saved", len(events))
    if paper_enabled or cfg.mirror_strategy:
        st.warning("A separate paper-trading bot is enabled in your settings. These pages do not show whether it is running.")
    else:
        st.info("Order sending is off in your settings. You can explore the app and run a practice demo.")
    if killed:
        st.warning("The emergency stop file is present. The event bot will block new entries and try to close its paper positions.")
    age = news_age(events)
    if age is not None and age > 24:
        st.warning(f"The newest saved news is {age / 24:.0f} days old. This is not a current news feed. Try collecting news again.")
    heartbeat = storage.get("event-heartbeat")
    if heartbeat:
        st.caption(f"Last news collector check: {heartbeat}. This records a past check, not proof that the bot is running now.")
    done = [bool(st.session_state.get("paper_checked")), bool(events), not storage.runs().empty]
    st.subheader("Start with these three steps")
    st.progress(sum(done) / 3, text=f"{sum(done)} of 3 steps done · any order is fine; step 3 needs no setup")

    def step(n, title):
        st.markdown(f"**{'✅' if done[n - 1] else f'{n}.'} {title}**")

    with st.container(border=True):
        step(1, "Check your data connection")
        st.write("Alpaca supplies prices and news. Checking connects to your paper account without placing a trade.")
        if not credentials:
            st.info("Alpaca credentials are missing. Open Help → Connection setup, then return here.")
        if st.button("Check Alpaca connection", disabled=not credentials):
            try:
                from botfolio.data.alpaca import AlpacaClient
                from botfolio.safety import check_environment

                check_environment()
                with st.spinner("Checking your paper account…"):
                    account = AlpacaClient(load_credentials(), timeout=15, max_retries=2).get_paper_account()
                if account.get("status") == "ACTIVE" and not account.get("trading_blocked") and not account.get("account_blocked"):
                    st.session_state.paper_checked = True
                    st.success("Connected to an active paper account. No trade was placed.")
                else:
                    st.warning("The paper account responded, but it is not ready for trading. Check its status in Alpaca.")
            except Exception:
                log.exception("dashboard account check failed")
                st.error("We could not verify your paper account. Check your keys, internet connection and Alpaca account, then try again.")
    with st.container(border=True):
        step(2, "Collect company news")
        st.write(f"Check for articles from the last 24 hours about {', '.join(watchlist)}. This saves news; it does not place trades or call an AI model.")
        if st.button("Collect latest news", disabled=not credentials):
            try:
                with st.spinner("Checking for new articles…"):
                    inserted = collect_news(cfg, store)
                if inserted:
                    st.success(f"Saved {inserted} new article records. Open News & AI to read them.")
                else:
                    st.info("No new articles were saved. The provider may have returned no recent news, or those articles are already saved. This does not confirm a live feed.")
            except Exception:
                log.exception("dashboard news collection failed")
                st.error("News could not be collected. Check your connection and news access in Alpaca, then try again.")
    with st.container(border=True):
        step(3, "Try a practice demo")
        st.write("Test the existing chart strategies with $10,000 of simulated money and generated prices. No account or AI key is needed.")
        st.caption("The demo teaches you how to read results. It cannot tell you whether a strategy works in real markets.")
        if st.button("Run practice demo", type="primary"):
            try:
                with st.spinner("Running the practice test. This may take a few seconds…"):
                    run_id = practice_demo(cfg, storage)
                st.session_state.beginner_run = run_id
                st.success("Practice complete. Open Results in the sidebar to see what happened.")
            except Exception:
                log.exception("dashboard demo failed")
                st.error("The practice test could not finish. Your settings and existing results have been kept. Try again or check the application logs.")
    if not ai_ready:
        st.caption("Next, when you are ready: connect an AI provider using Help → AI setup. Until then, saved news has no new AI assessment.")


def render_news(store):
    st.title("News & AI")
    st.write("Read the saved news and see how the model interpreted it. An assessment is a research hypothesis, not an executed trade.")
    events = store.events()
    if not events:
        st.info("No news saved yet. Open Start here and choose Collect latest news.")
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
        with st.container(border=True):
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


def render_results(storage, store):
    st.title("Results, explained")
    st.write("Start with the chart-strategy tests. News and AI experiments are separate because their timing and exposure differ.")
    runs = storage.runs()
    if runs.empty:
        st.info("No chart-strategy results yet. Open Start here and run the practice demo.")
    else:
        choices = list(runs.run_id)
        default = st.session_state.get("beginner_run")
        labels = {row["run_id"]: run_label(row) for _, row in runs.iterrows()}
        run_id = st.selectbox("Choose a saved test", choices, index=choices.index(default) if default in choices else 0,
                             format_func=labels.get)
        run = runs.set_index("run_id").loc[run_id]
        if is_demo(run):
            st.warning("Practice demo — generated prices, not real market history. These results are for learning only.")
        elif run["status"] != "complete":
            st.info("This test has not finished. The results below may be incomplete.")
        else:
            st.info("Historical results describe this sample only. They do not establish future profitability.")
        stats = storage.stats(run_id)
        table = ranking_table(stats)
        if table.empty:
            st.info("This test has no summary yet. Choose another test or return after it finishes.")
        else:
            st.subheader("The short answer")
            summary = verdict_text(table)
            if summary:
                getattr(st, summary[0])(summary[1])
            st.caption("The luck check asks whether a result is too strong to be explained by chance. Random trading "
                       "and buy and hold are yardsticks: a useful strategy should beat both.")
            st.subheader("How did each strategy do?")
            ordered = table.sort_values("total_return_pct")
            fig = go.Figure()
            for group, label in GROUPS.items():
                part = ordered[ordered.group == group]
                if len(part):
                    fig.add_trace(go.Bar(x=part.total_return_pct, y=[friendly(n) for n in part.index], orientation="h",
                                         name=label, marker_color=GROUP_COLORS[group],
                                         hovertemplate="%{y}: %{x:+.2f}%<extra></extra>"))
            fig.add_vline(x=0, line_color="#8a8985", line_width=1)
            fig.update_layout(height=max(300, 24 * len(ordered)), margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="Money gained or lost after costs (%)", barmode="overlay",
                              yaxis=dict(categoryorder="array", categoryarray=[friendly(n) for n in ordered.index]),
                              legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0))
            st.plotly_chart(fig, width="stretch")
            st.caption("Bars right of the line made money; bars left of it lost money.")
            with st.expander("See the numbers as a table"):
                simple = table.reindex(columns=["group", "n_trades", "total_return_pct", "max_drawdown_pct", "win_rate"]).copy()
                simple["group"] = simple.group.map(GROUPS).fillna(simple.group)
                simple.index = [friendly(n) for n in simple.index]
                simple.index.name = "Strategy"
                simple.columns = ["Type", "Trades", "Money gained or lost (%)", "Largest drop (%)", "Winning trades (%)"]
                st.dataframe(simple, column_config={col: st.column_config.NumberColumn(col, format="%.2f")
                             for col in simple.columns if "(%)" in col}, width="stretch")
                st.caption("Winning often does not necessarily mean making money: a few big losses can outweigh many small wins.")
            st.subheader("Look closer at one strategy")
            names = list(table.index)
            selected = st.selectbox("Strategy", names, format_func=friendly)
            metric = stats[selected]
            bench = table[table.group == "benchmark"].total_return_pct.mean()
            a, b, c = st.columns(3)
            a.metric("Return after costs", f"{metric['total_return_pct']:+.2f}%",
                     None if pd.isna(bench) or selected.startswith("buy_hold")
                     else f"{metric['total_return_pct'] - bench:+.2f} pts vs buy and hold")
            b.metric("Trades made", int(metric["n_trades"]), help="Fewer than 30 trades is too few to judge a strategy.")
            c.metric("Largest account drop", f"{metric['max_drawdown_pct']:.2f}%",
                     help="The largest measured decline from a previous peak. Losses between measurements may be larger.")
            equity = storage.equity(run_id)
            compare = [selected] + [n for n in names if n.startswith("buy_hold") and n != selected][:1]
            curves = equity[equity.portfolio.isin(compare)]
            if not curves.empty:
                chart = curves.pivot_table(index="ts", columns="portfolio", values="equity").ffill()
                st.line_chart(chart.rename(columns=friendly), y_label="Simulated account value ($)")
                capital = json.loads(run["config"])["risk"]["starting_capital"]
                st.caption(f"Each strategy started with ${capital:,.0f}. Above that line is a gain, below is a loss. "
                           "Buy and hold is shown for comparison.")
            if not is_demo(run):
                st.write("**What can I conclude?** More testing on untouched data is needed. Choosing the best-looking result after trying many rules can give a misleading impression of skill.")
            trades = storage.trades(run_id)
            st.download_button("Download this test's trades", trades.to_csv(index=False), f"{run_id}-trades.csv", "text/csv")
    st.divider()
    st.subheader("News and AI experiments")
    reports = store.reports()
    if reports.empty:
        st.info("No news trading tests have been completed. Saved articles and WAIT decisions are not trading results.")
        return
    run_id = st.selectbox("Choose a news experiment", list(reports.run_id))
    payload = json.loads(reports.set_index("run_id").loc[run_id, "payload"])
    st.warning("Exploratory research — no demonstrated profitable edge. Historical AI may know events that happened later.")
    summaries = pd.DataFrame(payload["summaries"]).T
    if summaries.empty:
        st.info("No trades were taken. The model may have abstained, or suitable data may be missing.")
    else:
        st.dataframe(summaries.reindex(columns=["n", "total_return_pct", "max_drawdown_pct", "conclusion"]).rename(columns={
            "n": "Trade records", "total_return_pct": "Return after costs (%)", "max_drawdown_pct": "Closed-account drop (%)",
            "conclusion": "What the evidence supports"}), width="stretch")
        st.caption("Labels combine strategy, holding time in minutes and data split. Random rows are matched controls, not independent portfolios.")
    with st.expander("Advanced: experiment settings, exclusions and all results"):
        st.json(payload)
    st.download_button("Download news experiment", json.dumps(payload, indent=2), f"{run_id}.json", "application/json")


def render_paper(cfg, storage):
    from botfolio.events.paper import configured

    st.title("Paper trading control room")
    st.write("Actual orders at Alpaca's paper broker, using simulated money. Real-money trading is not supported.")
    watchlist = storage.get("event-watchlist") or cfg.symbols
    st.session_state.setdefault("event_watchlist_symbols", ", ".join(watchlist))
    with st.form("event_watchlist"):
        value = st.text_input("News watchlist", key="event_watchlist_symbols",
            help="Enter up to 20 ticker symbols, separated by commas. This selects news coverage, not a recommendation to buy.")
        if st.form_submit_button("Save watchlist"):
            symbols = list(dict.fromkeys(part.strip().upper() for part in value.split(",") if part.strip()))
            if not symbols or len(symbols) > 20 or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol) for symbol in symbols):
                st.error("Enter 1–20 valid ticker symbols separated by commas.")
            else:
                storage.put("event-watchlist", symbols)
                st.success("Watchlist saved. The analysis worker will use it on its next collection cycle.")
    now = datetime.now(UTC)
    calendar = get_calendar("NASDAQ")
    session = calendar.session_for(now)
    upcoming = next((s for s in calendar.sessions_between(ny_date(now), ny_date(now)+timedelta(days=10))
                     if s.open > now), None)
    state = storage.get("event-execution-state") or {}
    healthy = bool(state and 0 <= (now-datetime.fromisoformat(state["at"])).total_seconds() <= 20
                   and state.get("phase") != "error")
    armed = bool(storage.get("event-paper-armed"))
    a, b, c = st.columns(3)
    a.metric("Nasdaq regular session", "Open" if session else "Closed")
    b.metric("Execution monitor", "Recent check" if healthy else "Not ready")
    c.metric("New paper entries", "Enabled" if configured() and armed else "Paused")
    if session:
        st.caption(f"Session closes {session.close.astimezone(ZoneInfo('America/New_York')):%H:%M} Eastern. Entries stop five minutes before closing; time exits begin two minutes before closing.")
    elif upcoming:
        st.caption(f"Next regular open: {upcoming.open.astimezone(ZoneInfo('America/New_York')):%a %d %b, %H:%M} Eastern. Holidays, early closes and daylight saving are included.")
    st.info("Limits: $10,000 experimental capital · 0.25% risk allowance per trade · 25% position limit · one long position · $100 daily loss cutoff. Stops can gap beyond the allowance.")
    data = storage.get("event-data-status") or {}
    latest = data.get("latest_content")
    stale_news = not latest or now-datetime.fromisoformat(latest) > timedelta(minutes=5)
    if stale_news:
        st.warning("No current news is available. Old articles cannot trigger new entries, even when paper trading is enabled.")
    if data.get("provider_failures"):
        st.error("A data provider failed its last check. Entries are blocked for that collection cycle.")
    if data.get("issuer_feed_failures"):
        st.warning("One or more issuer news feeds failed their last check.")
    if state.get("message"):
        st.error(state["message"])
    if not configured():
        st.info("Broker execution is disabled in this deployment. The app operator must enable paper execution and confirm a dedicated paper account before entries can be armed.")
    ai_ready = os.getenv("EVENT_AI_PROVIDER", "disabled") != "disabled" and bool(os.getenv("EVENT_AI_KEY"))
    with st.container(border=True):
        st.markdown("**Turn new paper trades on or off**")
        if configured() and armed:
            idle = ([] if session else ["the market is closed"]) + (["there is no fresh news"] if stale_news else [])
            st.success("**Paper entries are ON.** The bot will place a paper trade when fresh news gives a valid signal "
                       "during market hours." + (f" Right now it is waiting because {' and '.join(idle)}." if idle else ""))
        else:
            st.info("**Paper entries are OFF.** The bot will not open new trades.")
        dedicated = st.checkbox("I confirm this is a dedicated Alpaca paper account with no manual or other bot trading.")
        ready = [("Paper execution switched on by the app operator", configured()),
                 ("Background worker checked in within the last 20 seconds", healthy),
                 ("AI news reader set up", ai_ready), ("Dedicated paper account confirmed (box above)", dedicated)]
        if not all(ok for _, ok in ready):
            st.markdown("The **Enable** button unlocks when every item is ticked:\n\n" +
                        "\n".join(f"- {'✅' if ok else '❌'} {label}" for label, ok in ready))
        left, right = st.columns(2)
        if left.button("Enable paper entries", disabled=armed or not all(ok for _, ok in ready), type="primary"):
            storage.put("event-paper-armed", True)
            st.rerun()
        if right.button("Pause new entries", disabled=not armed):
            storage.put("event-paper-armed", False)
            st.rerun()
        st.caption("Pausing stops new trades only; positions already open are still managed and closed normally.")
    kill = Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL"))
    with st.container(border=True):
        st.markdown("**🛑 Emergency stop**")
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
    check = storage.get("event-last-entry-check")
    if check:
        st.write("Latest entry check", check)
    with storage.tx() as conn:
        rows = conn.execute("SELECT event_key,payload FROM event_orders").fetchall()
    if rows:
        st.subheader("Broker order journal")
        st.dataframe(pd.DataFrame([dict(event=key, **json.loads(payload)) for key, payload in rows]), hide_index=True)
    else:
        st.info("No broker orders have been recorded. The system waits for a fresh eligible event and a valid signal.")
    st.caption("Use Refresh page to update status. A recent check is not an uptime guarantee. Paper trading does not establish live profitability.")


def render_help():
    st.title("A quick guide")
    st.markdown("""
**New here? Follow this path:**

1. **Start here:** check the Alpaca connection and run a practice demo.
2. **Results:** see whether the strategies made or lost simulated money.
3. **News & AI:** read saved articles and the reasons behind any assessments.
4. **Paper trading:** inspect session status, pause entries or stop the paper bot.
5. **Advanced research:** use detailed charts and statistics when you are ready.

**Nothing to see yet?** Collect news from Start here. If the provider has no recent articles,
older articles stay labelled as archived. To learn the interface immediately, use the practice demo.

**Does WAIT mean something went wrong?** Usually it means no trade is suggested. Check the reason:
AI may be disabled, evidence may be weak, or uncertainty may be high.

**Is this making real-money trades?** This system supports Alpaca paper trading only. The dashboard
collects data and runs simulations. A separately configured background bot manages paper orders.
""")
    with st.expander("Plain-language glossary", expanded=True):
        st.markdown("""
- **Ticker:** a short market code, such as NVDA for Nvidia or SPY for a stock-market fund.
- **Strategy:** rules that decide when to enter and leave a trade.
- **Backtest:** a simulation using past prices and stated assumptions.
- **Paper trading:** practice orders using pretend money at a broker.
- **Return:** how much the simulated account gained or lost, as a percentage.
- **Trading costs:** fees and price differences incurred when buying and selling.
- **Drawdown:** a fall from a previous account peak; smaller is generally better.
- **Random baseline:** trades chosen by chance, used to check whether a strategy added value.
- **Buy and hold:** buying once and keeping the investment for comparison.
- **Confidence interval:** a range showing uncertainty in an estimate, not a guarantee.
- **Test data:** data kept separate from choosing and tuning the strategy.
""")
    with st.expander("Connection setup"):
        st.write("Create an Alpaca paper account and add its paper API key and secret to this app's .env file. Keep them private. Restart the dashboard after changing settings, then choose Check Alpaca connection on Start here.")
        st.code("ALPACA_KEY=your_paper_key\nALPACA_SECRET=your_paper_secret", language="bash")
        st.caption("Credentials are never displayed in this dashboard. Ask whoever runs the app for help editing its settings.")
    with st.expander("AI setup"):
        st.write("Alpaca credentials supply data; they do not include an AI model. An AI provider needs its own access key, supported model and endpoint. Provider usage may cost money. Ask the app operator to configure these settings; collection and the practice demo work without them.")
        st.code("EVENT_AI_PROVIDER=chat\nEVENT_AI_MODEL=your_model\nEVENT_AI_URL=https://your-provider.example/chat/completions\nEVENT_AI_KEY=your_private_key", language="bash")
        st.caption("For Clef or Jev, use the systemone adapter and the provider's documented endpoint. Keep AI disabled until configuration is complete.")
    st.info("A successful connection or a positive AI assessment does not show that the strategy is profitable.")


def render_beginner_page(page, cfg, storage, store: EventStore):
    if page == "Start here":
        render_home(cfg, storage, store)
    elif page == "News & AI":
        render_news(store)
    elif page == "Results":
        render_results(storage, store)
    elif page == "Paper trading":
        render_paper(cfg, storage)
    elif page == "Help":
        render_help()
