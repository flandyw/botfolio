"""Order Block.

Definition:
  A bullish order block is the LAST bearish candle (close < open) before the displacement leg that
  produced a bullish structure break (see structure.py):
    1. On a bullish break at bar ``k`` of swing high ``S`` (pivot index ``s``), find the leg origin =
       the bar with the lowest low in (s, k]   (the start of the up-move that broke ``S``).
    2. Scan backwards from the origin (inclusive) up to ``lookback`` bars for the first bearish candle.
    3. The zone is [low, high] of that candle.
  Bearish order blocks mirror this (last bullish candle before the down-leg that broke a swing low).
  The order block exists from the close of bar ``k``; nothing before ``k`` uses it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ttbot.data.models import Bar
from ttbot.patterns.structure import StructureBreak


@dataclass(slots=True)
class OrderBlock:
    direction: Literal[1, -1]
    bottom: float
    top: float
    created_index: int
    candle_index: int
    touched: bool = False


def find_order_block(
    history: Sequence[Bar], first_index: int, brk: StructureBreak, lookback: int = 10
) -> OrderBlock | None:
    """``history[j]`` is the bar with absolute index ``first_index + j``; the break bar must be the last."""

    def get(i: int) -> Bar | None:
        j = i - first_index
        return history[j] if 0 <= j < len(history) else None

    start, end = brk.swing.index + 1, brk.index
    leg: list[tuple[int, Bar]] = []
    for i in range(start, end + 1):
        b_ = get(i)
        if b_ is not None:
            leg.append((i, b_))
    if not leg:
        return None
    if brk.direction == 1:
        origin = min(leg, key=lambda t: (t[1].low, -t[0]))[0]
    else:
        origin = max(leg, key=lambda t: (t[1].high, t[0]))[0]
    for i in range(origin, max(origin - lookback, first_index - 1), -1):
        b = get(i)
        if b is None:
            break
        if (brk.direction == 1 and b.is_bear) or (brk.direction == -1 and b.is_bull):
            return OrderBlock(brk.direction, b.low, b.high, brk.index, i)
    return None
