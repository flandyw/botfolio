"""Streaming (incremental) indicators. Each ``update`` uses only the current and past values, so the
same objects give identical results in backtests and live trading (no look-ahead, no repainting)."""

from __future__ import annotations

import math


class EMA:
    """Exponential moving average seeded with the SMA of the first ``period`` values (TA-Lib convention).
    ``value`` is None until ``period`` values have been seen."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self._n = 0
        self._sum = 0.0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        self._n += 1
        if self.value is None:
            self._sum += x
            if self._n == self.period:
                self.value = self._sum / self.period
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


class WilderRSI:
    """RSI with Wilder smoothing (the original definition). Value is None for the first ``period`` closes."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev: float | None = None
        self._n = 0
        self._gain_sum = 0.0
        self._loss_sum = 0.0
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        change = close - self._prev
        self._prev = close
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if self._avg_gain is None or self._avg_loss is None:
            self._n += 1
            self._gain_sum += gain
            self._loss_sum += loss
            if self._n < self.period:
                return None
            self._avg_gain = self._gain_sum / self.period
            self._avg_loss = self._loss_sum / self.period
        else:
            p = self.period
            self._avg_gain = (self._avg_gain * (p - 1) + gain) / p
            self._avg_loss = (self._avg_loss * (p - 1) + loss) / p
        if self._avg_loss == 0.0:
            self.value = 100.0 if self._avg_gain > 0 else 50.0
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = 100.0 - 100.0 / (1.0 + rs)
        return self.value


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
