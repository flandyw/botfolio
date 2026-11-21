"""The most important property: a strategy's decision at time t must not change when future bars are
removed. We run each strategy on the full stream and on truncated prefixes and require identical signals."""

import pytest

from botfolio.config import load_config
from botfolio.data.aggregate import BarAggregator
from botfolio.strategies import build_roster


def _signals(strategy, bars):
    agg = BarAggregator(strategy.timeframe)
    out = []
    for b in bars:
        seq = []
        if strategy.timeframe > 1 and (d := agg.flush_before(b)) is not None:
            seq.append(d)
        if (d := agg.add(b)) is not None:
            seq.append(d)
        for x in seq:
            s = strategy.on_bar(x)
            if s:
                out.append((s.symbol, s.ts, s.side, round(s.ref_price, 8), s.stop))
    return out


@pytest.mark.parametrize("cut", [700, 1500, 2600])
def test_signals_identical_on_truncated_history(rw_bars, cut):
    cfg = load_config()
    full_roster = build_roster(cfg)
    trunc_roster = build_roster(cfg)
    prefix = rw_bars[:cut]
    horizon = prefix[-1].end
    checked = 0
    for full, trunc in zip(full_roster, trunc_roster, strict=True):
        a = [s for s in _signals(full, rw_bars) if s[1] <= horizon]
        b = _signals(trunc, prefix)
        # signals from the final partial window may legitimately be absent from the prefix run
        a = [s for s in a if s[1] <= horizon - __import__("datetime").timedelta(minutes=full.timeframe)]
        b = [s for s in b if s[1] <= horizon - __import__("datetime").timedelta(minutes=full.timeframe)]
        assert a == b, f"{full.name} repaints / looks ahead"
        checked += len(a)
    assert checked > 5  # noise_mom + random only signal a few times per day


def test_every_signal_is_after_its_bar_closes(rw_bars):
    for strat in build_roster(load_config()):
        agg = BarAggregator(strat.timeframe)
        for b in rw_bars[:3000]:
            seq = []
            if strat.timeframe > 1 and (d := agg.flush_before(b)) is not None:
                seq.append(d)
            if (d := agg.add(b)) is not None:
                seq.append(d)
            for x in seq:
                s = strat.on_bar(x)
                if s:
                    assert s.ts == x.end and s.ref_price == x.close
