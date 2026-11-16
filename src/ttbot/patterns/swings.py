"""Swing (fractal pivot) detection - the building block of every SMC/ICT definition.

Definition (deterministic, non-repainting):
  Bar ``i`` is a swing high when
      high[i] >  max(high[i-left .. i-1])   (strictly higher than the ``left`` bars before it), and
      high[i] >= max(high[i+1 .. i+right])  (not exceeded by the ``right`` bars after it).
  Swing lows mirror this with lows.
  The pivot is only CONFIRMED at the close of bar ``i+right``; it is reported then and never changed.
  (Strict on the left / non-strict on the right makes equal-high ties resolve to the first bar.)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ttbot.data.models import Bar


@dataclass(frozen=True, slots=True)
class SwingPoint:
    kind: Literal["high", "low"]
    price: float
    index: int  # bar index of the pivot itself
    ts: datetime
    confirmed_index: int  # bar index at which the pivot became known (index + right)


class SwingDetector:
    def __init__(self, left: int = 3, right: int = 3) -> None:
        if left < 1 or right < 1:
            raise ValueError("left and right must be >= 1")
        self.left = left
        self.right = right
        self._buf: deque[tuple[int, Bar]] = deque(maxlen=left + right + 1)

    def update(self, index: int, bar: Bar) -> list[SwingPoint]:
        """Feed bar ``index``; returns swing points confirmed by this bar (0, 1 or 2)."""
        self._buf.append((index, bar))
        if len(self._buf) < self.left + self.right + 1:
            return []
        items = list(self._buf)
        ci, cb = items[self.left]
        before = [b for _, b in items[: self.left]]
        after = [b for _, b in items[self.left + 1 :]]
        out: list[SwingPoint] = []
        if cb.high > max(b.high for b in before) and cb.high >= max(b.high for b in after):
            out.append(SwingPoint("high", cb.high, ci, cb.ts, index))
        if cb.low < min(b.low for b in before) and cb.low <= min(b.low for b in after):
            out.append(SwingPoint("low", cb.low, ci, cb.ts, index))
        return out

    def reset(self) -> None:
        self._buf.clear()
