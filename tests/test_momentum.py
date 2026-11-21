from datetime import timedelta

from ttbot.strategies.momentum import NoiseAreaMomentumStrategy

from .conftest import DAY, make_bars


def _run(strat, bars):
    return [s for b in bars if (s := strat.on_bar(b))]


def _session(cal, day, prices):
    return make_bars(cal, [(p, p, p, p) for p in prices], day=day, symbol="QQQ")


def test_breakout_entry_and_band_exit(cal):
    s = NoiseAreaMomentumStrategy("noise_1m", 1, {"lookback": 1, "step": 30})
    prev_day = DAY - timedelta(days=1)
    # previous session: price drifts 0.1% above the open -> sigma ~ 0.1% at every minute
    sigs = _run(s, _session(cal, prev_day, [100.0] + [100.1] * 389))
    assert sigs == []
    # today opens at the prior close; +1% by minute 29 (breakout), back to the open by minute 89
    today = [100.1] * 10 + [101.1] * 70 + [100.1] * 310
    sigs = _run(s, _session(cal, DAY, today))
    assert [(x.side, x.exit, x.reverse, x.ts.minute) for x in sigs[:2]] == [(1, False, True, 0), (1, True, False, 0)]
    first, second = (b for b in _session(cal, DAY, today) if b.minute_of_session in (29, 89))
    assert sigs[0].ts == first.end and sigs[1].ts == second.end


def test_short_below_lower_band(cal):
    s = NoiseAreaMomentumStrategy("noise_1m", 1, {"lookback": 1, "step": 30})
    _run(s, _session(cal, DAY - timedelta(days=1), [100.0] + [100.1] * 389))
    sigs = _run(s, _session(cal, DAY, [100.1] * 10 + [99.0] * 380))
    assert sigs[0].side == -1 and not sigs[0].exit
    assert len(sigs) == 1  # stays short until the portfolio flattens at the close


def test_no_signals_before_lookback_is_filled(rw_bars):
    s = NoiseAreaMomentumStrategy("noise_1m", 1, {"lookback": 14}, ["QQQ"])
    sigs = _run(s, rw_bars)
    sessions = sorted({b.session for b in rw_bars})
    assert all(x.session == sessions[-1] for x in sigs)  # 15 sessions: only the last one can trade
    assert all((x.ts.minute % 30) == 0 for x in sigs)
