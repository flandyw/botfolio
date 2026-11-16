"""Classic indicator strategies: EMA crossover, RSI divergence, opening-range breakout."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ttbot.data.models import Bar
from ttbot.indicators import EMA, WilderRSI
from ttbot.patterns.swings import SwingDetector, SwingPoint
from ttbot.strategies.base import BaseState, Signal, Strategy


# ---------------------------------------------------------------- EMA cross
@dataclass
class _EMAState:
    fast: EMA
    slow: EMA
    prev_diff: float | None = None


class EMACrossStrategy(Strategy[_EMAState]):
    """Long when EMA(fast) crosses ABOVE EMA(slow) on a bar close; short on the cross below.
    A cross requires sign(fast-slow) to change between two consecutive closes (zero counts as no side).
    No structural stop -> ATR stop."""

    kind = "ema_cross"
    defaults = {"fast": 9, "slow": 21}

    def new_state(self) -> _EMAState:
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("fast EMA period must be < slow")
        return _EMAState(EMA(self.params["fast"]), EMA(self.params["slow"]))

    def evaluate(self, base: BaseState, st: _EMAState, bar: Bar, new_session: bool) -> Signal | None:
        f, s = st.fast.update(bar.close), st.slow.update(bar.close)
        if f is None or s is None:
            return None
        diff = f - s
        prev, st.prev_diff = st.prev_diff, diff
        if prev is None or not base.atr.value:
            return None
        if prev <= 0 < diff:
            return self.signal(base, bar, 1, fast=f, slow=s)
        if prev >= 0 > diff:
            return self.signal(base, bar, -1, fast=f, slow=s)
        return None


# ------------------------------------------------------------ RSI divergence
@dataclass
class _DivState:
    rsi: WilderRSI
    swings: SwingDetector
    rsi_hist: deque[float | None] = field(default_factory=lambda: deque(maxlen=400))
    last_low: tuple[SwingPoint, float] | None = None
    last_high: tuple[SwingPoint, float] | None = None


class RSIDivergenceStrategy(Strategy[_DivState]):
    """Regular RSI divergence at confirmed swing pivots.

    Bullish: two consecutive confirmed swing lows P1, P2 with
        price(P2) < price(P1), RSI(P2) > RSI(P1), RSI(P2) <= ``oversold``,
        and ``min_sep`` <= index(P2) - index(P1) <= ``max_sep``.
    RSI is read at the pivot bars themselves (already closed). The signal fires at P2's confirmation
    bar (P2 + right), so it never uses unconfirmed pivots. Stop = price(P2). Bearish mirrors with highs.
    """

    kind = "rsi_divergence"
    defaults = {
        "rsi_period": 14,
        "left": 3,
        "right": 3,
        "min_sep": 5,
        "max_sep": 60,
        "oversold": 40.0,
        "overbought": 60.0,
    }

    def new_state(self) -> _DivState:
        p = self.params
        return _DivState(WilderRSI(p["rsi_period"]), SwingDetector(p["left"], p["right"]))

    def evaluate(self, base: BaseState, st: _DivState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        st.rsi_hist.append(st.rsi.update(bar.close))
        sig: Signal | None = None
        for sp in st.swings.update(base.index, bar):
            back = base.index - sp.index
            if back >= len(st.rsi_hist):
                continue
            r = st.rsi_hist[-1 - back]
            if r is None:
                continue
            if sp.kind == "low":
                prev = st.last_low
                st.last_low = (sp, r)
                if prev and p["min_sep"] <= sp.index - prev[0].index <= p["max_sep"]:
                    if sp.price < prev[0].price and r > prev[1] and r <= p["oversold"]:
                        sig = self.signal(base, bar, 1, stop=sp.price, rsi1=prev[1], rsi2=r)
            else:
                prev = st.last_high
                st.last_high = (sp, r)
                if prev and p["min_sep"] <= sp.index - prev[0].index <= p["max_sep"]:
                    if sp.price > prev[0].price and r < prev[1] and r >= p["overbought"]:
                        sig = self.signal(base, bar, -1, stop=sp.price, rsi1=prev[1], rsi2=r)
        return sig if base.atr.value else None


# ------------------------------------------------------- opening range breakout
@dataclass
class _ORBState:
    high: float | None = None
    low: float | None = None
    traded: bool = False
    complete: bool = False


class OpeningRangeBreakoutStrategy(Strategy[_ORBState]):
    """Opening range = high/low of all bars starting in the first ``or_minutes`` of the session.
    After the range completes, the first bar that CLOSES above the high goes long (below the low goes
    short). One trade per session. Stop = opposite side of the range (``stop`` = "opposite") or its
    midpoint ("mid")."""

    kind = "orb"
    defaults = {"or_minutes": 15, "stop": "opposite"}

    def new_state(self) -> _ORBState:
        return _ORBState()

    def evaluate(self, base: BaseState, st: _ORBState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        if new_session:
            st.high = st.low = None
            st.traded = st.complete = False
        if bar.minute_of_session < p["or_minutes"]:
            st.high = bar.high if st.high is None else max(st.high, bar.high)
            st.low = bar.low if st.low is None else min(st.low, bar.low)
            st.complete = bar.minute_of_session + bar.timeframe >= p["or_minutes"]
            return None
        if st.high is None or st.low is None or st.traded or not base.atr.value:
            return None
        mid = (st.high + st.low) / 2
        if bar.close > st.high:
            st.traded = True
            stop = st.low if p["stop"] == "opposite" else mid
            return self.signal(base, bar, 1, stop=stop, or_high=st.high, or_low=st.low)
        if bar.close < st.low:
            st.traded = True
            stop = st.high if p["stop"] == "opposite" else mid
            return self.signal(base, bar, -1, stop=stop, or_high=st.high, or_low=st.low)
        return None
