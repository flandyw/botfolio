"""The vectorised null-model exit simulator must agree with the event-driven Portfolio."""

import numpy as np
import pytest

from botfolio.config import load_config
from botfolio.execution.costs import CostModel
from botfolio.execution.portfolio import Portfolio
from botfolio.montecarlo import RandomEntryNull, build_arrays, eligible_entries, simulate_exits
from botfolio.strategies.base import Signal


@pytest.mark.parametrize("tf", [1, 5])
def test_vectorised_exits_match_portfolio(rw_bars, tf):
    cfg = load_config()
    cfg = cfg.model_copy(update={"exits": cfg.exits.model_copy(update={"stop_mode": "atr", "atr_stop_mult": 1.2}),
                                 "costs": cfg.costs.model_copy(update={"min_commission": 0.0})})  # null ignores the minimum
    bars = [b for b in rw_bars if b.symbol == "SPY"]
    arrays = build_arrays(bars)["SPY"]
    idx, atr = eligible_entries(bars, tf, cfg.exits.atr_period, cfg.exits.no_entry_last_minutes)["SPY"]
    rng = np.random.default_rng(3)
    pick = np.sort(rng.choice(len(idx), 40, replace=False))
    for side in (1, -1):
        vec = simulate_exits(arrays, idx[pick], atr[pick], side, 1.2, cfg.exits.reward_risk,
                             cfg.exits.max_hold_bars * tf, cfg)
        for k, j in enumerate(pick):
            e = int(idx[j])
            pf = Portfolio("x", tf, cfg.risk, cfg.exits, CostModel(cfg.costs))
            prev = bars[e - 1]
            pf.pending[prev.symbol] = type("P", (), {"signal": Signal(
                "x", "SPY", side, bars[e].ts, prev.close, float(atr[j]), None, session=bars[e].session)})()
            i = e
            while not pf.closed:
                b = bars[i]
                pf.on_bar(b)
                if b.is_last and not pf.closed:
                    pf.flatten("SPY", b.end, b.close, "eod")
                i += 1
            assert pf.closed[0].return_pct == pytest.approx(vec[k], abs=2e-6), (side, e, pf.closed[0])


def test_null_pvalue_is_calibrated_for_random_means(rw_bars):
    cfg = load_config()
    null = RandomEntryNull(rw_bars, cfg)
    longs, shorts = null.outcomes(1, 1.5, 2.0)
    res_mean = null.test(float(np.mean(np.concatenate([longs, shorts]))), 100, 0.5, 1, 1.5, 2.0, 500, 1)
    assert 0.2 < res_mean["p_mc"] < 0.8
    res_high = null.test(5.0, 100, 0.5, 1, 1.5, 2.0, 500, 1)
    assert res_high["p_mc"] < 0.01


def test_null_uses_strategy_exits_and_symbols(rw_bars, cfg):
    null = RandomEntryNull(rw_bars, cfg)
    base_l, _ = null.outcomes(1, 1.5, 2.0)
    short_l, _ = null.outcomes(1, 1.5, 2.0, cfg.exits.model_copy(update={"max_hold_bars": 1}))
    assert len(short_l) == len(base_l) and not np.allclose(short_l, base_l)
    none_l, _ = null.outcomes(1, 1.5, 2.0, symbols=["NOT_A_SYMBOL"])
    assert len(none_l) == 0
