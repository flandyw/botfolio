from ttbot.patterns.fvg import detect_fvg
from ttbot.patterns.order_block import find_order_block
from ttbot.patterns.structure import StructureTracker
from ttbot.patterns.swings import SwingDetector

from .conftest import make_bars


def test_swing_high_confirmed_only_after_right_bars(cal):
    highs = [10, 11, 12, 15, 13, 12, 11, 12]
    bars = make_bars(cal, [(h - 0.5, h, h - 1, h - 0.5) for h in highs])
    det = SwingDetector(2, 2)
    found = {}
    for i, b in enumerate(bars):
        for sp in det.update(i, b):
            found.setdefault(sp.kind, []).append((sp.index, sp.confirmed_index))
    assert (3, 5) in found["high"]  # pivot at bar 3 known only at bar 5


def test_swing_ties_resolve_to_first_bar(cal):
    highs = [10, 11, 15, 15, 12, 11, 10]
    bars = make_bars(cal, [(h - 0.5, h, h - 1, h - 0.5) for h in highs])
    det = SwingDetector(2, 2)
    pivots = [sp.index for i, b in enumerate(bars) for sp in det.update(i, b) if sp.kind == "high"]
    assert pivots == [2]


def test_fvg_bullish_and_bearish(cal):
    a, b, c = make_bars(cal, [(100, 101, 99.5, 100.8), (100.8, 103, 100.7, 102.9), (102.9, 104, 101.6, 103.5)])
    g = detect_fvg(a, b, c, 2, atr=1.0, min_gap_atr=0.25, min_body_atr=0.5)
    assert g is not None and g.direction == 1 and g.bottom == 101 and g.top == 101.6
    a, b, c = make_bars(cal, [(100, 100.5, 99, 99.2), (99.2, 99.3, 96.5, 96.6), (96.6, 97.9, 96, 96.2)])
    g = detect_fvg(a, b, c, 2, atr=1.0, min_gap_atr=0.25, min_body_atr=0.5)
    assert g is not None and g.direction == -1 and g.bottom == 97.9 and g.top == 99


def test_fvg_filters(cal):
    a, b, c = make_bars(cal, [(100, 101, 99.5, 100.8), (100.8, 103, 100.7, 102.9), (102.9, 104, 101.05, 103.5)])
    assert detect_fvg(a, b, c, 2, atr=1.0, min_gap_atr=0.25) is None  # gap 0.05 < 0.25 ATR
    a, b, c = make_bars(cal, [(100, 101, 99.5, 100.8), (101, 103, 100.7, 101.1), (102.9, 104, 101.6, 103.5)])
    assert detect_fvg(a, b, c, 2, atr=1.0, min_gap_atr=0.25, min_body_atr=0.5) is None  # weak middle candle


def _zigzag(cal):
    # up to a swing high (bar 3), pull back to a swing low (bar 6), rally and close above the high (bar 9)
    closes = [100, 101, 102, 103, 102, 101, 100, 101, 102, 104, 105]
    ohlc = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        ohlc.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
    return make_bars(cal, ohlc)


def test_structure_bos_then_choch(cal):
    bars = _zigzag(cal)
    tr = StructureTracker(2, 2)
    breaks = [(i, b) for i, bar in enumerate(bars) if (b := tr.update(i, bar))]
    assert breaks, "expected a break"
    i, brk = breaks[0]
    assert brk.direction == 1 and brk.kind == "BOS" and i == 9 and brk.swing.index == 3
    # now dump below the swing low -> bearish break against an up-trend = CHoCH
    more = make_bars(cal, [(105, 105.2, 104, 104.1), (104.1, 104.2, 99, 99.1)], start_minute=len(bars))
    out = [tr.update(len(bars) + k, b) for k, b in enumerate(more)]
    chochs = [b for b in out if b]
    assert chochs and chochs[-1].kind == "CHoCH" and chochs[-1].direction == -1


def test_order_block_is_last_down_candle_before_leg(cal):
    bars = _zigzag(cal)
    tr = StructureTracker(2, 2)
    for i, bar in enumerate(bars):
        brk = tr.update(i, bar)
        if brk:
            ob = find_order_block(bars[: i + 1], 0, brk, lookback=10)
            assert ob is not None
            assert ob.candle_index == 6  # lowest bar of the pull-back, a down candle
            assert ob.bottom == bars[6].low and ob.top == bars[6].high
            return
    raise AssertionError("no break")
