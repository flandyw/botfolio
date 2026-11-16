"""Streamlit dashboard: rankings, equity curves, trades, significance and live status.

Run with:  ttbot dashboard     (or: streamlit run dashboard/app.py)
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ttbot.analysis import compute_stats, ranking_table
from ttbot.config import load_config
from ttbot.storage import Storage

# Reference categorical palette (fixed order, validated for colour-vision deficiency on adjacent pairs).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED = "#8a8985"
GRID = "rgba(128,128,128,0.18)"
MAX_LINES = len(SERIES)

st.set_page_config(page_title="TikTok Strategy Lab", page_icon="📉", layout="wide")


@st.cache_resource
def get_storage(path: str) -> Storage:
    return Storage(path)


def _layout(fig: go.Figure, title: str, y_title: str, height: int = 420) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15)),
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        yaxis_title=y_title,
    )
    fig.update_xaxes(showgrid=False, showline=True, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=True, zerolinecolor=GRID)
    return fig


def fmt_pct(x: float) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2f}%"


cfg = load_config()
db_path = st.sidebar.text_input("SQLite database", os.environ.get("TTBOT_DB_PATH", cfg.db_path))
storage = get_storage(db_path)
runs = storage.runs()
if runs.empty:
    st.title("TikTok Strategy Lab")
    st.info("No runs yet. Run a backtest first:  `ttbot backtest --start 2025-01-02 --end 2025-12-31`")
    st.stop()

labels = {r.run_id: f"{r.run_id}  ·  {r.mode}  ·  {r.status}" for r in runs.itertuples()}
run_id = st.sidebar.selectbox("Run", list(labels), format_func=lambda k: labels[k])
run = runs.set_index("run_id").loc[run_id]
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
    st.rerun()

trades = storage.trades(run_id)
equity = storage.equity(run_id)
stats = storage.stats(run_id)
if not stats:  # forward run before the first nightly stats refresh: compute without Monte Carlo
    names = sorted(set(equity["portfolio"]).union(trades["portfolio"])) if len(equity) or len(trades) else []
    stats = compute_stats(cfg, trades, equity, names) if names else {}
table = ranking_table(stats)

groups = ["pattern", "inverse", "random", "benchmark"]
sel_groups = st.sidebar.multiselect("Strategy groups", groups, default=groups)
tfs = st.sidebar.multiselect("Timeframes (min)", [1, 5], default=[1, 5])
if not table.empty:
    table = table[table["group"].isin(sel_groups) & (table["timeframe"].isin(tfs) | (table["group"] == "benchmark"))]

st.title("TikTok Strategy Lab")
st.caption(
    f"Run **{run_id}** · mode `{run['mode']}` · {run['start']} → {run['end'] or 'now'} · {run['notes'] or ''}. "
    "Paper trading only. Every portfolio starts with identical capital and identical cost/risk rules."
)

if table.empty:
    st.warning("No statistics for this selection yet.")
    st.stop()

# ----------------------------------------------------------------- KPI row
pat = table[table["group"] == "pattern"]
rnd = table[table["group"] == "random"]
bh = table[table["group"] == "benchmark"]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Pattern strategies", len(pat))
c2.metric("Significant edge (FDR 5%)", int((table["verdict"] == "significant edge").sum()))
c3.metric("Best pattern return", fmt_pct(pat["total_return_pct"].max()) if len(pat) else "–",
          help=str(pat["total_return_pct"].idxmax()) if len(pat) else None)
c4.metric("Random baseline (avg)", fmt_pct(rnd["total_return_pct"].mean()) if len(rnd) else "–")
c5.metric("Buy & hold (avg)", fmt_pct(bh["total_return_pct"].mean()) if len(bh) else "–")

tab_rank, tab_eq, tab_sig, tab_trades, tab_live, tab_help = st.tabs(
    ["Rankings", "Equity curves", "Significance", "Trades", "Live status", "How to read this"]
)

# ------------------------------------------------------------------ rankings
with tab_rank:
    cols = ["rank", "group", "timeframe", "n_trades", "win_rate", "profit_factor", "avg_return_pct",
            "gross_avg_return_pct", "avg_cost_pct", "expectancy_r", "total_return_pct", "sharpe", "sortino",
            "max_drawdown_pct", "p_ttest", "q_ttest", "p_mc", "q_mc", "verdict"]
    cols = [c for c in cols if c in table.columns]
    st.dataframe(
        table[cols],
        width="stretch",
        height=min(38 * (len(table) + 1), 900),
        column_config={
            "win_rate": st.column_config.NumberColumn("win %", format="%.1f"),
            "profit_factor": st.column_config.NumberColumn("profit factor", format="%.2f"),
            "avg_return_pct": st.column_config.NumberColumn("avg net %/trade", format="%.4f"),
            "gross_avg_return_pct": st.column_config.NumberColumn("avg gross %/trade", format="%.4f"),
            "avg_cost_pct": st.column_config.NumberColumn("avg cost %/trade", format="%.4f"),
            "expectancy_r": st.column_config.NumberColumn("expectancy (R)", format="%.3f"),
            "total_return_pct": st.column_config.NumberColumn("total return %", format="%.2f"),
            "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
            "sortino": st.column_config.NumberColumn("Sortino", format="%.2f"),
            "max_drawdown_pct": st.column_config.NumberColumn("max DD %", format="%.2f"),
            "p_ttest": st.column_config.NumberColumn("p (t-test)", format="%.4f"),
            "q_ttest": st.column_config.NumberColumn("q (t, FDR)", format="%.4f"),
            "p_mc": st.column_config.NumberColumn("p (vs random)", format="%.4f"),
            "q_mc": st.column_config.NumberColumn("q (vs random, FDR)", format="%.4f"),
        },
    )
    st.caption("Ranked by Sharpe ratio. *q* values are Benjamini–Hochberg adjusted across all tested strategies.")

    # pattern vs inverse vs random comparison
    pt = table[table["group"].isin(["pattern", "inverse", "random"])].sort_values("avg_return_pct")
    fig = go.Figure(
        go.Bar(
            x=pt["avg_return_pct"],
            y=pt.index,
            orientation="h",
            marker=dict(color=[SERIES[0] if g == "pattern" else SERIES[1] if g == "inverse" else MUTED
                               for g in pt["group"]], cornerradius=4),
            customdata=pt[["n_trades", "win_rate"]].to_numpy(),
            hovertemplate="%{y}<br>avg net %{x:.4f}%/trade<br>%{customdata[0]} trades, win %{customdata[1]:.1f}%<extra></extra>",
        )
    )
    fig.add_vline(x=0, line_color=MUTED, line_width=1)
    _layout(fig, "Average net return per trade (after costs)", "", height=max(320, 22 * len(pt)))
    fig.update_layout(hovermode="closest", xaxis_title="% of notional per trade")
    st.plotly_chart(fig, width="stretch")
    st.caption("Blue = pattern strategy · orange = its inverse · gray = random baseline.")

# --------------------------------------------------------------- equity curves
with tab_eq:
    names = list(table.index)
    default = []
    if len(pat):
        best = pat.index[0]
        default = [best] + ([f"inv_{best}"] if f"inv_{best}" in names else [])
    default += [n for n in names if n.startswith("random")][:2] + [n for n in names if n.startswith("buy_hold")][:2]
    chosen = st.multiselect(f"Strategies (up to {MAX_LINES})", names, default=default[:MAX_LINES],
                            max_selections=MAX_LINES)
    mode = st.radio("Curve", ["Daily equity", "Cumulative net P&L by trade"], horizontal=True)
    fig = go.Figure()
    for i, name in enumerate(chosen):
        color = SERIES[i % len(SERIES)]
        if mode == "Daily equity":
            e = equity[equity["portfolio"] == name]
            if e.empty:
                continue
            fig.add_trace(go.Scatter(x=e["ts"], y=e["equity"], name=name, mode="lines",
                                     line=dict(color=color, width=2)))
        else:
            t = trades[trades["portfolio"] == name].sort_values("exit_ts")
            if t.empty:
                continue
            fig.add_trace(go.Scatter(x=t["exit_ts"], y=t["net_pnl"].cumsum(), name=name, mode="lines",
                                     line=dict(color=color, width=2, shape="hv")))
    if mode == "Daily equity":
        fig.add_hline(y=cfg.risk.starting_capital, line_color=MUTED, line_dash="dot", line_width=1)
    _layout(fig, mode, "$")
    st.plotly_chart(fig, width="stretch")

    dd_rows = []
    for name in chosen:
        e = equity[equity["portfolio"] == name].set_index("ts")["equity"]
        if len(e):
            dd_rows.append((name, (e / e.cummax() - 1) * 100))
    if dd_rows:
        fig = go.Figure()
        for i, (name, dd) in enumerate(dd_rows):
            fig.add_trace(go.Scatter(x=dd.index, y=dd, name=name, mode="lines",
                                     line=dict(color=SERIES[i % len(SERIES)], width=2)))
        _layout(fig, "Drawdown (daily closes)", "% from peak", height=300)
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- significance
with tab_sig:
    sig = table[table["group"].isin(["pattern", "inverse", "random"])].copy()
    sig = sig.dropna(subset=["avg_return_pct"]).sort_values("avg_return_pct")
    if sig.empty:
        st.info("Not enough trades for significance tests.")
    else:
        fig = go.Figure()
        if {"null_p05", "null_p95"}.issubset(sig.columns):
            fig.add_trace(go.Bar(
                y=sig.index, x=sig["null_p95"] - sig["null_p05"], base=sig["null_p05"], orientation="h",
                marker=dict(color="rgba(138,137,133,0.28)"), name="random-entry null (5–95%)",
                hovertemplate="%{y}<br>null 5–95%%: %{base:.4f} … %{x:.4f} wide<extra></extra>",
            ))
        if {"ci_low", "ci_high"}.issubset(sig.columns):
            fig.add_trace(go.Scatter(
                y=sig.index, x=sig["avg_return_pct"], mode="markers", name="strategy mean (95% bootstrap CI)",
                marker=dict(color=SERIES[0], size=9, line=dict(color="white", width=2)),
                error_x=dict(type="data", symmetric=False, array=sig["ci_high"] - sig["avg_return_pct"],
                             arrayminus=sig["avg_return_pct"] - sig["ci_low"], color=SERIES[0], thickness=2),
                customdata=sig[["p_mc", "q_mc", "n_trades"]].to_numpy() if "p_mc" in sig else None,
                hovertemplate="%{y}<br>mean %{x:.4f}%/trade<br>p vs random %{customdata[0]:.4f} · q %{customdata[1]:.4f}"
                              "<br>%{customdata[2]} trades<extra></extra>",
            ))
        fig.add_vline(x=0, line_color=MUTED, line_width=1)
        _layout(fig, "Is the pattern better than random entries with the same exits and costs?",
                "", height=max(380, 24 * len(sig)))
        fig.update_layout(hovermode="closest", barmode="overlay", xaxis_title="mean net return per trade (%)")
        st.plotly_chart(fig, width="stretch")
        st.markdown(
            "A strategy has a credible edge only if its **whole confidence interval is right of zero** *and* its "
            "mean sits **right of the gray random-entry band**, with FDR-adjusted q < 0.05. "
            "The gray band is computed only for historical runs (it needs the bar data)."
        )

# ---------------------------------------------------------------------- trades
with tab_trades:
    names = sorted(trades["portfolio"].unique()) if len(trades) else []
    pick = st.selectbox("Strategy", ["(all)"] + names)
    t = trades if pick == "(all)" else trades[trades["portfolio"] == pick]
    if len(t):
        h = go.Figure(go.Histogram(x=t["return_pct"], nbinsx=60, marker=dict(color=SERIES[0]),
                                   hovertemplate="%{x}<br>%{y} trades<extra></extra>"))
        h.add_vline(x=0, line_color=MUTED, line_width=1)
        _layout(h, "Distribution of net return per trade", "trades", height=280)
        h.update_layout(xaxis_title="% of notional", hovermode="closest", bargap=0.08)
        st.plotly_chart(h, width="stretch")
        by_reason = t.groupby("exit_reason").agg(trades=("net_pnl", "size"), avg_return_pct=("return_pct", "mean"),
                                                 net_pnl=("net_pnl", "sum"))
        st.dataframe(by_reason, width="stretch")
    show = t.sort_values("exit_ts", ascending=False).head(2000)
    st.dataframe(
        show[["portfolio", "symbol", "side", "qty", "entry_ts", "entry_price", "exit_ts", "exit_price",
              "gross_pnl", "costs", "net_pnl", "return_pct", "r_multiple", "exit_reason"]],
        width="stretch", height=480,
    )
    st.download_button("Download trades (CSV)", t.to_csv(index=False), f"{run_id}-trades.csv", "text/csv")

# ------------------------------------------------------------------ live status
with tab_live:
    hb = storage.get_meta("heartbeat")
    if hb:
        ts = datetime.fromisoformat(hb[0])
        age = (datetime.now(UTC) - ts).total_seconds()
        st.metric("Last bot heartbeat", ts.strftime("%Y-%m-%d %H:%M:%S UTC"), f"{age / 60:.1f} min ago",
                  delta_color="off")
    else:
        st.info("The forward paper-trading bot has not run against this database yet.")
    live = storage.get(f"live_equity:{run_id}")
    if live:
        df = pd.DataFrame(sorted(live["equity"].items(), key=lambda kv: -kv[1]), columns=["portfolio", "equity"])
        df["return %"] = (df["equity"] / cfg.risk.starting_capital - 1) * 100
        st.caption(f"Mark-to-market at {live['ts']}")
        st.dataframe(df, width="stretch", hide_index=True)
    sigs = storage.signals(run_id, 300)
    st.subheader("Latest signals")
    st.dataframe(sigs.drop(columns=["run_id"]), width="stretch", height=360)

with tab_help:
    st.markdown(open(os.path.join(os.path.dirname(__file__), "HELP.md")).read())
