"""Intraday momentum outside the "noise area" (Zarattini, Aziz & Barbon 2024, "Beat the Market")."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace

from botfolio.data.models import Bar
from botfolio.strategies.base import BaseState, Signal, Strategy


@dataclass
class _NoiseState:
    moves: dict[int, deque[float]] = field(default_factory=dict)  # slot -> last N sessions' |move from open|
    open: float = 0.0
    prev_close: float | None = None
    last_close: float | None = None
    pv: float = 0.0
    vol: float = 0.0
    slot: int = 0
    pos: int = 0  # the side this strategy last asked for (the portfolio may have been stopped out since)


class NoiseBandMomentumStrategy(Strategy[_NoiseState]):
    """Trend-follow only when today's move is abnormally large for the time of day.

    At every ``interval``-minute mark, sigma = the mean absolute move from the open at this time of day
    over the previous ``lookback`` sessions (today excluded). The noise area is
    ``[min(open, prev_close) * (1 - k*sigma), max(open, prev_close) * (1 + k*sigma)]``.
    Long when the close is above the area AND above session VWAP; short mirrors. The position is closed
    at the first mark whose close is back inside max(upper band, VWAP) (a trailing stop), else at the
    session close. Signals are ``managed``: no profit target and no time stop; the hard stop is the far
    side of the noise area, which also sets the position size."""

    kind = "noise_momentum"
    defaults = {"lookback": 14, "band_mult": 1.0, "interval": 30}

    def new_state(self) -> _NoiseState:
        return _NoiseState()

    def evaluate(self, base: BaseState, st: _NoiseState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        if new_session:
            st.prev_close, st.open = st.last_close, bar.open
            st.pv = st.vol = 0.0
            st.slot = st.pos = 0
        st.last_close = bar.close
        st.pv += (bar.high + bar.low + bar.close) / 3.0 * bar.volume
        st.vol += bar.volume
        # first bar ending at/after each mark, so a missing minute cannot skip a decision
        slot = (bar.minute_of_session + bar.timeframe) // p["interval"]
        if slot <= st.slot:
            return None
        st.slot = slot
        hist = st.moves.setdefault(slot, deque(maxlen=p["lookback"]))
        sigma = sum(hist) / len(hist) if len(hist) == p["lookback"] else None
        hist.append(abs(bar.close / st.open - 1.0))  # today's move joins the average only after it is used
        if sigma is None or st.prev_close is None or not st.vol or not base.atr.value:
            return None
        vwap = st.pv / st.vol
        upper = max(st.open, st.prev_close) * (1.0 + p["band_mult"] * sigma)
        lower = min(st.open, st.prev_close) * (1.0 - p["band_mult"] * sigma)
        c = bar.close
        side, stop = 0, None
        if st.pos != 0:
            if (c >= max(upper, vwap)) if st.pos > 0 else (c <= min(lower, vwap)):
                return None  # trend intact
        elif c > upper and c > vwap:
            side, stop = 1, lower
        elif c < lower and c < vwap:
            side, stop = -1, upper
        else:
            return None
        st.pos = side
        return replace(self.signal(base, bar, side, stop=stop, upper=upper, lower=lower, vwap=vwap), managed=True)
