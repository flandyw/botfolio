"""ICT / Smart-Money-Concepts strategies: FVG, liquidity sweep, order block, BOS, CHoCH."""

from __future__ import annotations

from dataclasses import dataclass, field

from ttbot.data.models import Bar
from ttbot.patterns.fvg import FairValueGap, detect_fvg
from ttbot.patterns.order_block import OrderBlock, find_order_block
from ttbot.patterns.structure import StructureTracker
from ttbot.patterns.swings import SwingDetector, SwingPoint
from ttbot.strategies.base import BaseState, Signal, Strategy


# --------------------------------------------------------------------------- FVG
@dataclass
class _FVGState:
    gaps: list[FairValueGap] = field(default_factory=list)


class FVGStrategy(Strategy[_FVGState]):
    """Trade the first retrace into a fresh Fair Value Gap, in the gap's direction.

    Entry (long; short mirrors): on a later bar (same session, within ``expire_bars``) whose low trades
    into the gap (low <= top) and whose close holds the gap (close >= bottom). A close below the gap
    bottom invalidates it. Stop = far edge of the gap. One trade per gap.
    """

    kind = "fvg"
    defaults = {"min_gap_atr": 0.25, "min_body_atr": 0.5, "expire_bars": 30, "max_active": 3}

    def new_state(self) -> _FVGState:
        return _FVGState()

    def evaluate(self, base: BaseState, st: _FVGState, bar: Bar, new_session: bool) -> Signal | None:
        if new_session:
            st.gaps.clear()
        p = self.params
        sig: Signal | None = None
        # 1) test existing gaps (created on earlier bars)
        for g in list(st.gaps):
            if base.index - g.created_index > p["expire_bars"]:
                st.gaps.remove(g)
                continue
            if g.direction == 1:
                if bar.close < g.bottom:
                    st.gaps.remove(g)
                elif bar.low <= g.top and sig is None:
                    sig = self.signal(base, bar, 1, stop=g.bottom, gap_bottom=g.bottom, gap_top=g.top)
                    st.gaps.remove(g)
            else:
                if bar.close > g.top:
                    st.gaps.remove(g)
                elif bar.high >= g.bottom and sig is None:
                    sig = self.signal(base, bar, -1, stop=g.top, gap_bottom=g.bottom, gap_top=g.top)
                    st.gaps.remove(g)
        # 2) detect a new gap completed by this bar (all three candles in this session)
        h = base.history
        atr = base.atr.value
        if atr and len(h) >= 3 and h[-3].session == bar.session:
            new_gap = detect_fvg(h[-3], h[-2], h[-1], base.index, atr, p["min_gap_atr"], p["min_body_atr"])
            if new_gap:
                st.gaps.append(new_gap)
                st.gaps = st.gaps[-p["max_active"] :]
        return sig if atr else None


# ------------------------------------------------------------- liquidity sweep
@dataclass
class _SweepState:
    swings: SwingDetector
    highs: list[SwingPoint] = field(default_factory=list)
    lows: list[SwingPoint] = field(default_factory=list)


class LiquiditySweepStrategy(Strategy[_SweepState]):
    """Stop-hunt reversal.

    Liquidity = confirmed swing highs/lows (resting stops). A bullish sweep is a bar whose LOW trades
    below an active swing low but whose CLOSE is back above it -> long; stop = the sweep bar's low.
    A close beyond the level is a genuine break: the level is removed with no trade. Levels expire after
    ``max_age`` bars. Bearish sweeps mirror this.
    """

    kind = "liquidity_sweep"
    defaults = {"left": 5, "right": 5, "max_age": 120, "max_levels": 5}

    def new_state(self) -> _SweepState:
        return _SweepState(SwingDetector(self.params["left"], self.params["right"]))

    def evaluate(self, base: BaseState, st: _SweepState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        for lst in (st.highs, st.lows):
            lst[:] = [s for s in lst if base.index - s.confirmed_index <= p["max_age"]]
        sig: Signal | None = None
        swept_lows = [s for s in st.lows if bar.low < s.price]
        swept_highs = [s for s in st.highs if bar.high > s.price]
        if swept_lows:
            lvl = min(s.price for s in swept_lows)
            if bar.close > max(s.price for s in swept_lows) and not swept_highs:
                sig = self.signal(base, bar, 1, stop=bar.low, level=lvl)
            st.lows = [s for s in st.lows if s not in swept_lows]
        if swept_highs:
            lvl = max(s.price for s in swept_highs)
            if bar.close < min(s.price for s in swept_highs) and not swept_lows:
                sig = self.signal(base, bar, -1, stop=bar.high, level=lvl)
            st.highs = [s for s in st.highs if s not in swept_highs]
        for sp in st.swings.update(base.index, bar):
            (st.highs if sp.kind == "high" else st.lows).append(sp)
        st.highs = st.highs[-p["max_levels"] :]
        st.lows = st.lows[-p["max_levels"] :]
        return sig if base.atr.value else None


# ---------------------------------------------------------------- structure
@dataclass
class _StructState:
    tracker: StructureTracker


class StructureBreakStrategy(Strategy[_StructState]):
    """Trade a BOS (continuation) or CHoCH (reversal) in the direction of the break at the break bar's
    close. Stop = the most recent opposite swing (the structural invalidation level)."""

    kind = "structure"
    defaults = {"left": 3, "right": 3, "break_type": "BOS"}

    def new_state(self) -> _StructState:
        return _StructState(StructureTracker(self.params["left"], self.params["right"]))

    def evaluate(self, base: BaseState, st: _StructState, bar: Bar, new_session: bool) -> Signal | None:
        brk = st.tracker.update(base.index, bar)
        if brk is None or brk.kind != self.params["break_type"] or not base.atr.value:
            return None
        stop = brk.opposite_swing.price if brk.opposite_swing else None
        if stop is not None and (stop - bar.close) * brk.direction >= 0:
            stop = None  # protective swing on the wrong side -> fall back to ATR stop
        return self.signal(base, bar, brk.direction, stop=stop, level=brk.level, break_kind=brk.kind)


# -------------------------------------------------------------- order block
@dataclass
class _OBState:
    tracker: StructureTracker
    blocks: list[OrderBlock] = field(default_factory=list)


class OrderBlockStrategy(Strategy[_OBState]):
    """Trade the first retest of a fresh order block (see patterns/order_block.py).

    Entry (bullish; bearish mirrors): on a later bar (same session, within ``expire_bars``) whose low
    trades into the block (low <= top) and whose close stays above the block's bottom. A close below the
    bottom invalidates the block. Stop = block bottom. One trade per block.
    """

    kind = "order_block"
    defaults = {"left": 3, "right": 3, "lookback": 10, "expire_bars": 60, "max_active": 3}

    def new_state(self) -> _OBState:
        return _OBState(StructureTracker(self.params["left"], self.params["right"]))

    def evaluate(self, base: BaseState, st: _OBState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        if new_session:
            st.blocks.clear()
        sig: Signal | None = None
        for ob in list(st.blocks):
            if base.index - ob.created_index > p["expire_bars"]:
                st.blocks.remove(ob)
                continue
            if ob.direction == 1:
                if bar.close < ob.bottom:
                    st.blocks.remove(ob)
                elif bar.low <= ob.top and sig is None:
                    sig = self.signal(base, bar, 1, stop=ob.bottom, ob_bottom=ob.bottom, ob_top=ob.top)
                    st.blocks.remove(ob)
            else:
                if bar.close > ob.top:
                    st.blocks.remove(ob)
                elif bar.high >= ob.bottom and sig is None:
                    sig = self.signal(base, bar, -1, stop=ob.top, ob_bottom=ob.bottom, ob_top=ob.top)
                    st.blocks.remove(ob)
        brk = st.tracker.update(base.index, bar)
        if brk is not None:
            ob_new = find_order_block(list(base.history), base.first_index, brk, p["lookback"])
            if ob_new and base.history[ob_new.candle_index - base.first_index].session == bar.session:
                st.blocks.append(ob_new)
                st.blocks = st.blocks[-p["max_active"] :]
        return sig if base.atr.value else None
