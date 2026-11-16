"""Market structure: Break of Structure (BOS) and Change of Character (CHoCH).

Definitions (close-based to avoid wick ambiguity):
  * Keep the most recent CONFIRMED swing high and swing low (see swings.py) that have not been broken.
  * A bullish break occurs on the first bar whose CLOSE is above the active swing high.
    A bearish break occurs on the first bar whose CLOSE is below the active swing low.
  * The break is a CHoCH if it is against the prevailing trend (bullish break while trend == -1, or the
    very first break defines the trend and is labelled BOS), otherwise a BOS (continuation).
  * After a break the trend flips/continues and that swing level is consumed (cannot break twice).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ttbot.data.models import Bar
from ttbot.patterns.swings import SwingDetector, SwingPoint


@dataclass(frozen=True, slots=True)
class StructureBreak:
    direction: Literal[1, -1]
    kind: Literal["BOS", "CHoCH"]
    level: float
    swing: SwingPoint
    index: int
    opposite_swing: SwingPoint | None  # latest swing on the other side (protective level)


class StructureTracker:
    def __init__(self, left: int = 3, right: int = 3) -> None:
        self.swings = SwingDetector(left, right)
        self.trend: int = 0
        self.active_high: SwingPoint | None = None
        self.active_low: SwingPoint | None = None
        self.last_high: SwingPoint | None = None
        self.last_low: SwingPoint | None = None
        self.new_swings: list[SwingPoint] = []

    def update(self, index: int, bar: Bar) -> StructureBreak | None:
        # 1) evaluate breaks against levels known BEFORE this bar
        brk: StructureBreak | None = None
        if self.active_high is not None and bar.close > self.active_high.price:
            kind: Literal["BOS", "CHoCH"] = "CHoCH" if self.trend == -1 else "BOS"
            brk = StructureBreak(1, kind, self.active_high.price, self.active_high, index, self.last_low)
            self.trend = 1
            self.active_high = None
        elif self.active_low is not None and bar.close < self.active_low.price:
            kind = "CHoCH" if self.trend == 1 else "BOS"
            brk = StructureBreak(-1, kind, self.active_low.price, self.active_low, index, self.last_high)
            self.trend = -1
            self.active_low = None
        # 2) register pivots confirmed by this bar (usable from the NEXT bar on)
        self.new_swings = self.swings.update(index, bar)
        for sp in self.new_swings:
            if sp.kind == "high":
                self.active_high = sp
                self.last_high = sp
            else:
                self.active_low = sp
                self.last_low = sp
        return brk
