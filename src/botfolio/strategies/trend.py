"""Long-only ETF holding with a moving-average trend filter (Faber 2007, "A Quantitative Approach to
Tactical Asset Allocation"; Siegel's 200-day rule)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from botfolio.data.models import Bar
from botfolio.strategies.base import BaseState, Signal, Strategy


@dataclass
class _TrendState:
    closes: deque[float] = field(default_factory=deque)
    last_close: float | None = None
    want: int = 1  # invested until there is enough history, i.e. buy-and-hold by default


class TrendHoldStrategy(Strategy[_TrendState]):
    """Hold the ETF while yesterday's close is above its ``sma_days`` simple moving average of daily closes,
    otherwise sit in cash. A ``band_pct`` hysteresis band (enter above SMA*(1+band), leave below
    SMA*(1-band)) cuts whipsaw trades, which cost spread, slippage and the dividends missed while in cash.

    Decided once per session from the previous close and filled at the next minute's open. Positions are
    ``hold``: no stop, target, time stop or end-of-day flatten, so they collect dividends. The desired state
    is re-sent every session so the portfolio converges after a restart."""

    kind = "trend_hold"
    per_symbol = True  # one portfolio per ETF, comparable with buy_hold_<symbol>
    defaults = {"sma_days": 200, "band_pct": 1.0}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.warmup_sessions = int(self.params["sma_days"]) + 5

    def new_state(self) -> _TrendState:
        return _TrendState(closes=deque(maxlen=int(self.params["sma_days"])))

    def evaluate(self, base: BaseState, st: _TrendState, bar: Bar, new_session: bool) -> Signal | None:
        prev, st.last_close = st.last_close, bar.close
        if not new_session or prev is None:
            return None
        st.closes.append(prev)
        sma = None
        if len(st.closes) == st.closes.maxlen:
            sma = sum(st.closes) / len(st.closes)
            band = self.params["band_pct"] / 100.0
            if prev > sma * (1 + band):
                st.want = 1
            elif prev < sma * (1 - band):
                st.want = 0
        sig = self.signal(base, bar, st.want, prev_close=prev, sma=sma if sma is not None else "warming_up")
        return replace(sig, hold=True) if st.want else sig
