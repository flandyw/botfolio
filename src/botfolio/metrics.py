"""Performance metrics and statistical tests."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sstats

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough decline, in % (positive number)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(-dd.min() * 100.0)


def sharpe(daily_returns: pd.Series) -> float:
    """Annualised Sharpe ratio of daily returns (risk-free rate 0, sqrt(252) scaling)."""
    r = daily_returns.dropna()
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def sortino(daily_returns: pd.Series) -> float:
    r = daily_returns.dropna()
    downside = r[r < 0]
    if len(r) < 2 or len(downside) < 2:
        return 0.0
    dd = math.sqrt((downside**2).sum() / len(r))
    return float(r.mean() / dd * math.sqrt(TRADING_DAYS)) if dd > 0 else 0.0


def equity_series(equity_df: pd.DataFrame, trades: pd.DataFrame, starting_capital: float) -> pd.Series:
    """Combine daily snapshots with trade-close points for a finer drawdown estimate."""
    parts = []
    if not equity_df.empty:
        parts.append(equity_df.set_index("ts")["equity"])
    if not trades.empty:
        # equity after each trade = start + cumulative net P&L of intraday (flat-at-close) strategies
        parts.append(pd.Series(starting_capital + trades["net_pnl"].cumsum().values, index=trades["exit_ts"]))
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated(keep="last")]


def trade_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "win_rate": float("nan"), "profit_factor": float("nan"), "avg_return_pct": float("nan"),
                "expectancy_r": float("nan"), "avg_cost_pct": float("nan"), "gross_avg_return_pct": float("nan"),
                "long_frac": float("nan"), "avg_hold_min": float("nan")}
    pnl = trades["net_pnl"]
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    notional = (trades["qty"] * trades["entry_price"]).replace(0, np.nan)
    hold = (trades["exit_ts"] - trades["entry_ts"]).dt.total_seconds() / 60
    return {
        "n_trades": n,
        "win_rate": float((pnl > 0).mean() * 100),
        "profit_factor": float(wins / losses) if losses > 0 else float("inf") if wins > 0 else float("nan"),
        "avg_return_pct": float(trades["return_pct"].mean()),
        "gross_avg_return_pct": float((100 * trades["gross_pnl"] / notional).mean()),
        "avg_cost_pct": float((100 * trades["costs"] / notional).mean()),
        "expectancy_r": float(trades["r_multiple"].mean()),
        "total_net_pnl": float(pnl.sum()),
        "total_costs": float(trades["costs"].sum()),
        "long_frac": float((trades["side"] > 0).mean()),
        "avg_hold_min": float(hold.mean()),
    }


def portfolio_metrics(
    trades: pd.DataFrame, equity_df: pd.DataFrame, starting_capital: float
) -> dict[str, Any]:
    out = trade_metrics(trades)
    daily = equity_df.set_index("ts")["equity"] if not equity_df.empty else pd.Series(dtype=float)
    if not daily.empty:
        daily_full = pd.concat([pd.Series([starting_capital], index=[daily.index[0] - pd.Timedelta(days=1)]), daily])
        rets = daily_full.pct_change().dropna()
        final = float(daily.iloc[-1])
    else:
        rets = pd.Series(dtype=float)
        final = starting_capital
    eq = equity_series(equity_df, trades, starting_capital)
    out.update(
        {
            "final_equity": final,
            "total_return_pct": (final / starting_capital - 1) * 100,
            "sharpe": sharpe(rets),
            "sortino": sortino(rets),
            "max_drawdown_pct": max_drawdown(eq) if not eq.empty else 0.0,
            "n_days": len(daily),
        }
    )
    return out


def significance(returns_pct: np.ndarray, n_boot: int = 5000, seed: int = 0) -> dict[str, float]:
    """One-sided t-test (mean > 0) and bootstrap 95% CI of the mean per-trade net return."""
    r = returns_pct[np.isfinite(returns_pct)]
    if len(r) < 3:
        return {"t_stat": float("nan"), "p_ttest": float("nan"), "ci_low": float("nan"), "ci_high": float("nan")}
    t = sstats.ttest_1samp(r, 0.0, alternative="greater")
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n_boot, len(r)), replace=True).mean(axis=1) if len(r) * n_boot <= 5e7 else \
        np.array([rng.choice(r, len(r)).mean() for _ in range(n_boot)])
    return {
        "t_stat": float(t.statistic),
        "p_ttest": float(t.pvalue),
        "ci_low": float(np.percentile(means, 2.5)),
        "ci_high": float(np.percentile(means, 97.5)),
    }


def benjamini_hochberg(pvals: dict[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg FDR-adjusted q-values (NaNs are passed through)."""
    items = [(k, v) for k, v in pvals.items() if not math.isnan(v)]
    m = len(items)
    out = {k: float("nan") for k in pvals}
    if m == 0:
        return out
    items.sort(key=lambda kv: kv[1])
    q = [0.0] * m
    prev = 1.0
    for i in range(m - 1, -1, -1):
        prev = min(prev, items[i][1] * m / (i + 1))
        q[i] = prev
    for (k, _), qq in zip(items, q, strict=True):
        out[k] = qq
    return out
