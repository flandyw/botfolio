"""Intraday momentum on index ETFs: the "noise area" breakout of Zarattini, Aziz & Barbon (2024),
"Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)", SSRN 4824172."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from ttbot.data.models import Bar
from ttbot.strategies.base import BaseState, Signal, Strategy

SESSION_MINUTES = 390


@dataclass
class _NoiseState:
    moves: deque[list[float]]  # |close / session open - 1| by minute of session, one list per past session
    today: list[float] = field(default_factory=list)
    sigma: list[float] | None = None
    day_open: float | None = None
    prev_close: float | None = None
    last_close: float | None = None
    pv: float = 0.0
    vol: float = 0.0
    target: int = 0  # last desired position emitted: +1 long, -1 short, 0 flat


class NoiseAreaMomentumStrategy(Strategy[_NoiseState]):
    """Trade breakouts of the "noise area" around the session open, checked every ``step`` minutes.

    sigma(m) = mean over the previous ``lookback`` sessions of |close(m) / open - 1| at minute-of-session m.
    Upper band = max(open, previous close) * (1 + sigma(m)); lower band = min(open, previous close) * (1 - sigma(m)).
    At the close of each bar ending on a ``step``-minute boundary: long while close > upper band, short while
    close < lower band, flat otherwise (the band acts as a trailing stop). With ``use_vwap`` the long/short
    thresholds are max(upper, VWAP) / min(lower, VWAP), as in the paper; it is off by default because the
    free real-time feed (IEX) has unrepresentative volume. Positions are flat at the close (portfolio).
    Emits a signal only when the desired position changes: entries (``reverse``) or exits."""

    kind = "noise_area"
    defaults = {"lookback": 14, "step": 30, "use_vwap": False}

    @property
    def warmup_sessions(self) -> int:  # type: ignore[override]
        return int(self.params["lookback"]) + 1

    def new_state(self) -> _NoiseState:
        if self.params["lookback"] < 1 or self.params["step"] < 1:
            raise ValueError("lookback and step must be >= 1")
        return _NoiseState(moves=deque(maxlen=int(self.params["lookback"])))

    def _start_session(self, st: _NoiseState, bar: Bar) -> None:
        if st.day_open is not None:
            st.moves.append(st.today)
            st.prev_close = st.last_close
        st.today = [math.nan] * SESSION_MINUTES
        st.day_open = bar.open
        st.pv = st.vol = 0.0
        st.target = 0
        st.sigma = None
        if len(st.moves) == st.moves.maxlen:
            sigma = []
            for m in range(SESSION_MINUTES):
                vals = [d[m] for d in st.moves if not math.isnan(d[m])]
                sigma.append(sum(vals) / len(vals) if vals else math.nan)
            st.sigma = sigma

    def evaluate(self, base: BaseState, st: _NoiseState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        if new_session:
            self._start_session(st, bar)
        assert st.day_open is not None
        m = bar.minute_of_session
        if 0 <= m < SESSION_MINUTES:
            st.today[m] = abs(bar.close / st.day_open - 1.0)
        st.last_close = bar.close
        st.pv += (bar.high + bar.low + bar.close) / 3.0 * bar.volume
        st.vol += bar.volume
        if st.sigma is None or st.prev_close is None or bar.is_last or not base.atr.value:
            return None
        if (m + bar.timeframe) % p["step"] != 0 or not 0 <= m < SESSION_MINUTES:
            return None
        sig = st.sigma[m]
        if math.isnan(sig):
            return None
        upper = max(st.day_open, st.prev_close) * (1.0 + sig)
        lower = min(st.day_open, st.prev_close) * (1.0 - sig)
        if p["use_vwap"] and st.vol > 0:
            vwap = st.pv / st.vol
            upper, lower = max(upper, vwap), min(lower, vwap)
        want = 1 if bar.close > upper else -1 if bar.close < lower else 0
        if want == st.target:
            return None
        prev, st.target = st.target, want
        if want == 0:
            return self.signal(base, bar, prev, exit=True, upper=upper, lower=lower)
        return self.signal(base, bar, want, reverse=True, upper=upper, lower=lower)
