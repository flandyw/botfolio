"""Turn a run's trades/equity into rankings and significance tests."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import numpy as np
import pandas as pd

from ttbot.config import AppConfig
from ttbot.data.models import Bar
from ttbot.metrics import benjamini_hochberg, portfolio_metrics, significance
from ttbot.montecarlo import RandomEntryNull
from ttbot.strategies import strategy_group

log = logging.getLogger(__name__)


def _tf_of(name: str) -> int:
    for tf in (5, 1):
        if name.endswith(f"_{tf}m"):
            return tf
    return 1


def _stop_atr(trades: pd.DataFrame, default: float) -> float:
    vals = []
    for m in trades["meta"]:
        try:
            v = (json.loads(m) if isinstance(m, str) else m).get("stop_atr")
        except (ValueError, AttributeError):
            v = None
        if v is not None and math.isfinite(v):
            vals.append(v)
    return float(np.median(vals)) if vals else default


def compute_stats(
    cfg: AppConfig,
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    portfolios: list[str],
    bars: list[Bar] | None = None,
) -> dict[str, dict[str, Any]]:
    """Metrics for every portfolio; adds the random-entry Monte-Carlo test when ``bars`` are given."""
    null = RandomEntryNull(bars, cfg) if bars else None
    out: dict[str, dict[str, Any]] = {}
    for name in portfolios:
        t = trades[trades["portfolio"] == name].sort_values("exit_ts") if not trades.empty else trades
        e = equity[equity["portfolio"] == name] if not equity.empty else equity
        group = strategy_group(name)
        m: dict[str, Any] = portfolio_metrics(t, e, cfg.risk.starting_capital)
        m["group"] = group
        m["timeframe"] = _tf_of(name)
        if group == "benchmark":
            out[name] = m
            continue
        m.update(significance(t["return_pct"].to_numpy(dtype=float) if len(t) else np.array([]), seed=cfg.seed))
        if null is not None and len(t) >= 3:
            rr = cfg.exits.reward_risk
            rr_eff = 1.0 / rr if name.startswith("inv_") else rr
            stop_atr = _stop_atr(t, cfg.exits.atr_stop_mult)
            m["null_stop_atr"] = stop_atr
            m.update(
                null.test(
                    m["avg_return_pct"], len(t), m["long_frac"], m["timeframe"], stop_atr, rr_eff,
                    cfg.monte_carlo_iterations, cfg.seed,
                )
            )
        out[name] = m
    # multiple-testing correction across all non-benchmark strategies
    for key in ("p_ttest", "p_mc"):
        q = benjamini_hochberg({k: v.get(key, float("nan")) for k, v in out.items() if v["group"] != "benchmark"})
        for name, qv in q.items():
            out[name][key.replace("p_", "q_")] = qv
    for m_ in out.values():
        m_["verdict"] = verdict(m_)
    return out


def verdict(m: dict[str, Any], alpha: float = 0.05) -> str:
    if m.get("group") == "benchmark":
        return "benchmark"
    n = m.get("n_trades", 0)
    if n < 30:
        return "insufficient trades"
    q_t, q_mc = m.get("q_ttest", float("nan")), m.get("q_mc", float("nan"))
    if not math.isnan(q_t) and q_t < alpha and (math.isnan(q_mc) or q_mc < alpha):
        return "significant edge"
    if m.get("avg_return_pct", 0) > 0:
        return "positive, not significant"
    return "no edge"


def ranking_table(stats: dict[str, dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame.from_dict(stats, orient="index")
    df.index.name = "strategy"
    if df.empty:
        return df
    df = df.sort_values(["sharpe", "total_return_pct"], ascending=False)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df
