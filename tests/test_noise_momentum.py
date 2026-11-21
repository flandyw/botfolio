"""Noise-band momentum: entry only outside the noise area, strategy-managed exit, no target/time stop."""

from dataclasses import replace
from datetime import timedelta

from botfolio.engine import Engine
from botfolio.strategies.momentum import NoiseBandMomentumStrategy
from tests.conftest import DAY, make_bars


def _session(cal, day, closes):
    return make_bars(cal, [(c, c + 0.01, c - 0.01, c) for c in closes], day=day)


def _days(cal, n):
    out, d = [], DAY
    while len(out) < n:
        if cal.session(d) is not None:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_enters_breakout_and_exits_on_signal_not_target(cal, cfg):
    days = _days(cal, 4)
    quiet = [100.0 + 0.05 * (i % 2) for i in range(390)]  # |move from open| <= 0.05%
    # day 4: flat for 20 min, rallies 2% by 10:15 and holds until 13:00, then drops under VWAP
    trend = [100.0] * 20 + [100.0 + 2.0 * min(i, 25) / 25 for i in range(190)] + [101.5] * 180
    bars = [b for d in days[:3] for b in _session(cal, d, quiet)] + _session(cal, days[3], trend)

    strat = NoiseBandMomentumStrategy("noise_mom_1m", 1, {"lookback": 3})
    cfg = cfg.model_copy(update={"symbols": ["SPY"]})
    engine = Engine(cfg, [strat])
    for b in bars:
        engine.process(b)
    engine.end_session()
    trades = engine.drain().trades

    assert len(trades) == 1
    t = trades[0]
    assert t.side == 1 and t.exit_reason == "signal"
    assert t.meta["target"] is None  # a 2R target would have capped this trade long before the exit
    assert t.entry_ts.date() == days[3] and t.net_pnl > 0
    # entry at the first 30-minute mark after the breakout, exit at the first mark after the collapse
    assert (t.entry_ts - bars[-390].ts) == timedelta(minutes=30)
    assert (t.exit_ts - bars[-390].ts) == timedelta(minutes=240)


def test_no_trade_inside_noise_area(cal):
    days = _days(cal, 4)
    quiet = [100.0 + 0.05 * (i % 2) for i in range(390)]
    strat = NoiseBandMomentumStrategy("noise_mom_1m", 1, {"lookback": 3, "band_mult": 2.0})
    sigs = [strat.on_bar(b) for d in days for b in _session(cal, d, quiet)]
    assert not any(sigs)


def test_exit_signal_without_position_is_ignored(cal, cfg):
    bars = _session(cal, DAY, [100.0] * 30)
    strat = NoiseBandMomentumStrategy("noise_mom_1m", 1, {})
    engine = Engine(cfg, [strat])
    p = engine.portfolios[strat.name]
    for b in bars[:20]:
        engine.process(b)
    base = strat._states["SPY"][0]
    p.submit(replace(strat.signal(base, bars[19], 0), managed=True), bars[19])
    for b in bars[20:]:
        engine.process(b)
    assert not p.positions and not p.exit_pending and p.n_trades == 0
