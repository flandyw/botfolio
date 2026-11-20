from dataclasses import replace
from datetime import timedelta

import pytest

from ttbot.config import CostConfig, ExitConfig, RiskConfig
from ttbot.execution.costs import CostModel
from ttbot.execution.portfolio import Portfolio
from ttbot.strategies.base import Signal

from .conftest import make_bars

ZERO_COSTS = CostConfig(half_spread_per_share=0, slippage_bps=0, stop_slippage_bps=0, sec_fee_per_million=0,
                        finra_taf_per_share=0)


def _pf(costs=ZERO_COSTS, **exit_kw):
    ex = ExitConfig(stop_mode="structure", min_stop_atr=0.1, max_stop_atr=10, reward_risk=2.0,
                    no_entry_last_minutes=0, **exit_kw)
    return Portfolio("t", 1, RiskConfig(starting_capital=100_000, risk_per_trade_pct=1.0), ex, CostModel(costs))


def _sig(bar, side=1, stop=None, atr=1.0, inverse=False):
    return Signal("t", bar.symbol, side, bar.end, bar.close, atr, stop, inverse=inverse, session=bar.session)


def test_fill_at_next_open_and_target(cal):
    bars = make_bars(cal, [(100, 100.5, 99.5, 100), (100.2, 100.4, 100, 100.3), (100.3, 102.6, 100.2, 102.5)])
    pf = _pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1, stop=99.0), bars[0])
    assert not pf.positions  # nothing filled on the signal bar
    pf.on_bar(bars[1])
    pos = pf.positions["SPY"]
    assert pos.entry_fill == 100.2  # next bar's open
    assert pos.stop == pytest.approx(99.2) and pos.target == pytest.approx(102.2)
    assert pos.qty == 998  # risk size 1000 (1% of 100k / $1 stop) capped by no-leverage: 100k / 100.2
    pf.on_bar(bars[2])
    t = pf.closed[0]
    assert t.exit_reason == "target" and t.exit_price == pytest.approx(102.2)
    assert t.r_multiple == pytest.approx(2.0)


def test_stop_assumed_first_when_both_touched(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (100, 103, 98, 101)])
    pf = _pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1, stop=99.0), bars[0])
    pf.on_bar(bars[1])
    pf.on_bar(bars[2])
    assert pf.closed[0].exit_reason == "stop" and pf.closed[0].r_multiple == pytest.approx(-1)


def test_gap_through_stop_fills_at_open(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (97, 97.5, 96.5, 97)])
    pf = _pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1, stop=99.0), bars[0])
    pf.on_bar(bars[1])
    pf.on_bar(bars[2])
    t = pf.closed[0]
    assert t.exit_reason == "stop_gap" and t.exit_price == 97 and t.r_multiple == pytest.approx(-3)


def test_short_and_costs(cal):
    costs = CostConfig(half_spread_per_share=0.01, slippage_bps=0, stop_slippage_bps=0, sec_fee_per_million=0,
                       finra_taf_per_share=0)
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100.1, 97.0, 97.5)])
    pf = _pf(costs)
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], -1, stop=101.0), bars[0])
    pf.on_bar(bars[1])
    assert pf.positions["SPY"].entry_fill == pytest.approx(99.99)  # sold at the bid
    pf.on_bar(bars[2])
    t = pf.closed[0]
    assert t.exit_reason == "target" and t.side == -1
    assert t.net_pnl == pytest.approx(t.qty * 2.0)  # limit target, entry already paid half spread
    assert t.costs == pytest.approx(t.gross_pnl - t.net_pnl)
    assert pf.equity() == pytest.approx(100_000 + t.net_pnl)


def test_inverse_mirrors_exit_geometry(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100)])
    pf = _pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], -1, stop=99.0, inverse=True), bars[0])
    pf.on_bar(bars[1])
    pos = pf.positions["SPY"]
    assert pos.stop == pytest.approx(102.0) and pos.target == pytest.approx(99.0)


def test_time_stop_and_eod_flatten(cal):
    bars = make_bars(cal, [(100, 100, 100, 100)] * 6)
    pf = _pf(max_hold_bars=3)
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1, stop=99.0), bars[0])
    for b in bars[1:]:
        pf.on_bar(b)
    assert pf.closed[0].exit_reason == "time"
    assert pf.closed[0].exit_ts == bars[1].ts + timedelta(minutes=3)
    pf2 = _pf()
    pf2.on_bar(bars[0])
    pf2.submit(_sig(bars[0], 1, stop=99.0), bars[0])
    pf2.on_bar(bars[1])
    pf2.flatten("SPY", bars[1].end, 100.5, "eod")
    assert pf2.closed[0].exit_reason == "eod" and not pf2.positions


def test_signal_rejected_near_close_and_risk_limits(cal):
    s = cal.session(make_bars(cal, [(1, 1, 1, 1)])[0].session)
    last_minutes = int((s.close - s.open).total_seconds() // 60) - 2
    bars = make_bars(cal, [(100, 100, 100, 100)] * 2, start_minute=last_minutes)
    ex = ExitConfig(no_entry_last_minutes=15)
    pf = Portfolio("t", 1, RiskConfig(), ex, CostModel(ZERO_COSTS))
    pf.submit(_sig(bars[0], 1), bars[0])
    assert pf.rejections == {"too_close_to_close": 1}
    pf2 = Portfolio("t", 1, RiskConfig(allow_short=False), ExitConfig(no_entry_last_minutes=0), CostModel(ZERO_COSTS))
    pf2.submit(_sig(bars[0], -1), bars[0])
    assert pf2.rejections == {"short_disabled": 1}


def test_sizing_caps_notional(cal):
    bars = make_bars(cal, [(100, 100, 100, 100)] * 2)
    pf = _pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1, stop=99.99, atr=0.1), bars[0])  # tiny stop -> huge risk-based size
    pf.on_bar(bars[1])
    pos = pf.positions["SPY"]
    assert pos.qty * pos.entry_fill <= 100_000  # max_position_pct=100 -> no leverage


def test_regulatory_fees_on_sells_only():
    cm = CostModel(CostConfig())
    assert cm.fees(100, 500.0, 1) == 0.0
    assert cm.fees(100, 500.0, -1) == pytest.approx(100 * 500 * 27.80 / 1e6 + 100 * 0.000166)


def _flex_pf(**risk_kw):
    ex = ExitConfig(stop_mode="none", max_hold_bars=390, no_entry_last_minutes=0)
    risk = RiskConfig(starting_capital=100_000, sizing="notional", max_position_pct=100, **risk_kw)
    return Portfolio("t", 1, risk, ex, CostModel(ZERO_COSTS))


def test_notional_sizing_no_stop_and_exit_signal(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (50, 200, 1, 101), (102, 102, 102, 102)])
    pf = _flex_pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1), bars[0])
    pf.on_bar(bars[1])
    pos = pf.positions["SPY"]
    assert pos.qty == 1000 and pos.stop is None and pos.target is None
    pf.on_bar(bars[2])  # huge range: no stop/target to hit
    assert "SPY" in pf.positions
    pf.submit(replace(_sig(bars[2], 1), exit=True), bars[2])
    pf.on_bar(bars[3])
    t = pf.closed[0]
    assert t.exit_reason == "signal" and t.exit_price == 102 and t.net_pnl == pytest.approx(2000)


def test_reverse_signal_flips_position_at_next_open(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (99, 99, 99, 99), (99.5, 99.5, 99.5, 99.5)])
    pf = _flex_pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1), bars[0])
    pf.on_bar(bars[1])
    pf.submit(_sig(bars[1], 1), bars[1])  # same side while in position -> ignored
    assert pf.rejections == {"already_in_position": 1}
    pf.submit(replace(_sig(bars[2], -1), reverse=True), bars[2])
    pf.on_bar(bars[3])
    assert pf.closed[0].exit_reason == "reverse" and pf.closed[0].exit_price == 99.5
    assert pf.positions["SPY"].side == -1 and pf.positions["SPY"].entry_fill == 99.5


def test_reverse_to_short_when_long_only_just_exits(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (99.5, 99.5, 99.5, 99.5)])
    pf = _flex_pf(allow_short=False)
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1), bars[0])
    pf.on_bar(bars[1])
    pf.submit(replace(_sig(bars[1], -1), reverse=True), bars[1])
    pf.on_bar(bars[2])
    assert pf.closed[0].exit_reason == "signal" and pf.closed[0].exit_price == 99.5
    assert not pf.positions and not pf.rejections


def test_non_reverse_opposite_signal_is_still_ignored(cal):
    bars = make_bars(cal, [(100, 100, 100, 100), (100, 100, 100, 100), (99, 99, 99, 99)])
    pf = _flex_pf()
    pf.on_bar(bars[0])
    pf.submit(_sig(bars[0], 1), bars[0])
    pf.on_bar(bars[1])
    pf.submit(_sig(bars[1], -1), bars[1])
    pf.submit(replace(_sig(bars[1], -1), exit=True), bars[1])
    pf.on_bar(bars[2])
    assert pf.closed[0].exit_reason == "signal"
    assert not pf.positions and pf.rejections == {"already_in_position": 1}
