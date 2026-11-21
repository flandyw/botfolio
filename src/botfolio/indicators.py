"""Streaming (incremental) indicators. Each ``update`` uses only the current and past values, so the
same objects give identical results in backtests and live trading (no look-ahead, no repainting)."""

from __future__ import annotations

import math


class WilderATR:
    """Average True Range with Wilder smoothing, seeded by the mean of the first ``period`` true ranges."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self.value is None:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs) / self.period
                self._trs = []
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("non-finite ATR")
        return self.value
