"""Random-entry null model ("does this pattern beat a coin flip?").

For a strategy with K trades, a long-fraction f, a median stop distance of m x ATR and reward/risk rr,
the null hypothesis is that its entry TIMING is no better than random. We build the null distribution
of the mean per-trade net return by drawing K entry times uniformly from all eligible signal times
(same timeframe, same session filters), giving each a random side with P(long) = f, and applying the
SAME exit geometry and cost model used by the portfolio simulator (vectorised re-implementation of
``Portfolio.on_bar``; ``tests/test_montecarlo.py`` checks they agree).

p_mc = (1 + #{null means >= strategy mean}) / (N + 1)   (one-sided, add-one corrected).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from botfolio.config import AppConfig, ExitConfig
from botfolio.data.aggregate import BarAggregator
from botfolio.data.models import Bar
from botfolio.indicators import WilderATR


@dataclass
class SymbolArrays:
    symbol: str
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    ts_min: np.ndarray  # bar start, epoch minutes
    session: np.ndarray  # session ordinal
    is_last: np.ndarray


def build_arrays(bars: list[Bar]) -> dict[str, SymbolArrays]:
    by: dict[str, list[Bar]] = {}
    for b in bars:
        by.setdefault(b.symbol, []).append(b)
    out = {}
    for sym, bs in by.items():
        out[sym] = SymbolArrays(
            sym,
            np.array([b.open for b in bs]),
            np.array([b.high for b in bs]),
            np.array([b.low for b in bs]),
            np.array([b.close for b in bs]),
            np.array([int(b.ts.timestamp() // 60) for b in bs], dtype=np.int64),
            np.array([b.session.toordinal() if b.session else 0 for b in bs], dtype=np.int64),
            np.array([b.is_last for b in bs]),
        )
    return out


def eligible_entries(bars: list[Bar], tf: int, atr_period: int, no_entry_last_minutes: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per symbol: (1m entry indices, ATR known at the signal bar) for every possible signal bar."""
    by: dict[str, list[Bar]] = {}
    for b in bars:
        by.setdefault(b.symbol, []).append(b)
    out = {}
    for sym, bs in by.items():
        agg = BarAggregator(tf)
        atr = WilderATR(atr_period)
        idx: list[int] = []
        atrs: list[float] = []

        def consider(sig_bar: Bar, entry_i: int) -> None:
            atr.update(sig_bar.high, sig_bar.low, sig_bar.close)
            if (
                atr.value
                and entry_i < len(bs)
                and not sig_bar.is_last
                and sig_bar.minutes_to_close >= no_entry_last_minutes
                and bs[entry_i].session == sig_bar.session
            ):
                idx.append(entry_i)
                atrs.append(atr.value)

        for i, b in enumerate(bs):
            done = agg.flush_before(b) if tf > 1 else None
            if done is not None:
                consider(done, i)
            done = agg.add(b)
            if done is not None:
                consider(done, i + 1)
        out[sym] = (np.array(idx, dtype=np.int64), np.array(atrs))
    return out


def simulate_exits(
    a: SymbolArrays,
    entries: np.ndarray,
    atr: np.ndarray,
    side: int,
    stop_atr: float,
    rr: float,
    max_hold_minutes: int,
    cfg: AppConfig,
) -> np.ndarray:
    """Net return (% of entry notional) of a trade entered at the open of each 1m bar in ``entries``."""
    c = cfg.costs
    n = len(a.o)
    s = float(side)
    ref_e = a.o[entries]
    fill_e = ref_e + s * (c.half_spread_per_share + ref_e * c.slippage_bps / 1e4)
    d = stop_atr * atr
    stop = fill_e - s * d
    target = fill_e + s * rr * d
    deadline = a.ts_min[entries] + max_hold_minutes
    sess = a.session[entries]
    exit_ref = np.full(len(entries), np.nan)
    exit_kind = np.zeros(len(entries), dtype=np.int8)  # 1 market, 2 stop, 3 limit
    alive = np.ones(len(entries), dtype=bool)
    last_close = ref_e.copy()
    k = 0
    while alive.any():
        idx = entries + k
        inb = idx < n
        idx_c = np.minimum(idx, n - 1)
        same = inb & (a.session[idx_c] == sess)
        # ran out of session bars (missing final bar) -> exit at last known close
        gone = alive & ~same
        exit_ref[gone], exit_kind[gone] = last_close[gone], 1
        alive &= same
        if not alive.any():
            break
        o, h, lo, cl = a.o[idx_c], a.h[idx_c], a.lo[idx_c], a.c[idx_c]
        if side > 0:
            gap_stop, gap_tgt = o <= stop, o > target
            hit_stop, hit_tgt = lo <= stop, h > target
        else:
            gap_stop, gap_tgt = o >= stop, o < target
            hit_stop, hit_tgt = h >= stop, lo < target
        for mask, ref, kind in (
            (alive & gap_stop, o, 2),
            (alive & ~gap_stop & gap_tgt, o, 3),
        ):
            exit_ref[mask], exit_kind[mask] = ref[mask], kind
            alive &= ~mask
        m = alive & hit_stop
        exit_ref[m], exit_kind[m] = stop[m], 2
        alive &= ~m
        m = alive & hit_tgt
        exit_ref[m], exit_kind[m] = target[m], 3
        alive &= ~m
        m = alive & ((a.ts_min[idx_c] + 1) >= deadline)
        exit_ref[m], exit_kind[m] = cl[m], 1
        alive &= ~m
        m = alive & a.is_last[idx_c]
        exit_ref[m], exit_kind[m] = cl[m], 1
        alive &= ~m
        last_close = np.where(alive, cl, last_close)
        k += 1
    xs = -s
    adverse = np.where(
        exit_kind == 3,
        0.0,
        c.half_spread_per_share + exit_ref * (c.slippage_bps + np.where(exit_kind == 2, c.stop_slippage_bps, 0.0)) / 1e4,
    )
    fill_x = exit_ref + xs * adverse

    # shortcut: ignores min_commission (<0.5 bp/side at these position sizes), add it if small accounts are tested
    def fee_ps(price: np.ndarray, fside: float) -> np.ndarray:
        f = np.full_like(price, c.commission_per_share)
        if fside < 0:
            f = f + price * c.sec_fee_per_million / 1e6 + c.finra_taf_per_share
        return f

    net_ps = s * (fill_x - fill_e) - fee_ps(fill_e, s) - fee_ps(fill_x, xs)
    return 100.0 * net_ps / ref_e


class RandomEntryNull:
    """Caches eligible entries/outcomes so many strategies can be tested cheaply."""

    def __init__(self, bars: list[Bar], cfg: AppConfig) -> None:
        self.cfg = cfg
        self.bars = bars
        self.arrays = build_arrays(bars)
        self._elig: dict[tuple[int, int, int], dict[str, tuple[np.ndarray, np.ndarray]]] = {}
        self._out: dict[tuple[Any, ...], tuple[np.ndarray, np.ndarray]] = {}

    def outcomes(
        self,
        tf: int,
        stop_atr: float,
        rr: float,
        exits: ExitConfig | None = None,
        symbols: list[str] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        ex = exits or self.cfg.exits
        stop_atr, rr = round(stop_atr, 2), round(rr, 3)
        syms = tuple(sorted(symbols)) if symbols is not None else None
        ekey = (tf, ex.atr_period, ex.no_entry_last_minutes)
        key = (*ekey, ex.max_hold_bars, stop_atr, rr, syms)
        if key not in self._out:
            if ekey not in self._elig:
                self._elig[ekey] = eligible_entries(self.bars, tf, ex.atr_period, ex.no_entry_last_minutes)
            hold = ex.max_hold_bars * tf
            longs, shorts = [], []
            for sym, (idx, atr) in self._elig[ekey].items():
                if len(idx) == 0 or (syms is not None and sym not in syms):
                    continue
                a = self.arrays[sym]
                longs.append(simulate_exits(a, idx, atr, 1, stop_atr, rr, hold, self.cfg))
                shorts.append(simulate_exits(a, idx, atr, -1, stop_atr, rr, hold, self.cfg))
            self._out[key] = (
                np.concatenate(longs) if longs else np.array([]),
                np.concatenate(shorts) if shorts else np.array([]),
            )
        return self._out[key]

    def test(
        self,
        mean_return: float,
        n_trades: int,
        long_frac: float,
        tf: int,
        stop_atr: float,
        rr: float,
        iterations: int,
        seed: int,
        exits: ExitConfig | None = None,
        symbols: list[str] | None = None,
    ) -> dict[str, float]:
        longs, shorts = self.outcomes(tf, stop_atr, rr, exits, symbols)
        if n_trades < 3 or len(longs) == 0:
            return {"p_mc": float("nan"), "null_mean": float("nan"), "null_p05": float("nan"), "null_p95": float("nan")}
        rng = np.random.default_rng(seed)
        means = np.empty(iterations)
        chunk = max(1, int(2e7 // max(n_trades, 1)))
        for start in range(0, iterations, chunk):
            m = min(chunk, iterations - start)
            pick = rng.integers(0, len(longs), size=(m, n_trades))
            is_long = rng.random((m, n_trades)) < long_frac
            means[start : start + m] = np.where(is_long, longs[pick], shorts[pick]).mean(axis=1)
        return {
            "p_mc": float((1 + np.sum(means >= mean_return)) / (iterations + 1)),
            "null_mean": float(means.mean()),
            "null_p05": float(np.percentile(means, 5)),
            "null_p95": float(np.percentile(means, 95)),
        }

