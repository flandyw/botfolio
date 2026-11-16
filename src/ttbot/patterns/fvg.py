"""Fair Value Gap (3-candle imbalance).

Definition:
  For three consecutive bars A (i-2), B (i-1), C (i):
    Bullish FVG  if  low[C]  > high[A]   -> gap zone = [high[A], low[C]]
    Bearish FVG  if  high[C] < low[A]    -> gap zone = [high[C], low[A]]
  The gap must be at least ``min_gap_atr`` x ATR (filters tick-sized noise). Optionally the middle
  candle must be a displacement candle (body >= ``min_body_atr`` x ATR in the gap's direction).
  The FVG is known at the CLOSE of bar C.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ttbot.data.models import Bar


@dataclass(slots=True)
class FairValueGap:
    direction: Literal[1, -1]
    bottom: float
    top: float
    created_index: int
    mitigated: bool = False

    @property
    def size(self) -> float:
        return self.top - self.bottom


def detect_fvg(
    a: Bar, b: Bar, c: Bar, index: int, atr: float, min_gap_atr: float = 0.1, min_body_atr: float = 0.0
) -> FairValueGap | None:
    if atr <= 0:
        return None
    body = b.close - b.open  # displacement candle body (signed)
    if c.low > a.high:
        gap = c.low - a.high
        if gap >= min_gap_atr * atr and body >= min_body_atr * atr:
            return FairValueGap(1, a.high, c.low, index)
    if c.high < a.low:
        gap = a.low - c.high
        if gap >= min_gap_atr * atr and -body >= min_body_atr * atr:
            return FairValueGap(-1, c.high, a.low, index)
    return None
