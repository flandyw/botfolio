from datetime import date, timedelta

import pytest

from botfolio.engine import Engine
from botfolio.strategies.baselines import BuyAndHoldStrategy
from botfolio.strategies.trend import TrendHoldStrategy
from tests.conftest import make_bars

D1, D2 = date(2026, 9, 15), date(2026, 9, 16)


def _sessions(cal, closes, start=date(2026, 6, 1)):
    days = [s.day for s in cal.sessions_between(start, start + timedelta(days=60))][: len(closes)]
    return [make_bars(cal, [(c, c, c, c), (c, c, c, c)], d)[i] for d, c in zip(days, closes, strict=True) for i in (0, 1)]


def test_dividend_paid_on_ex_date_net_of_withholding(cal, cfg):
    cfg.costs.dividend_withholding_pct = 15
    engine = Engine(cfg, [BuyAndHoldStrategy("buy_hold_SPY", 1, {}, ["SPY"])])
    engine.dividends = {("SPY", D2): 2.0}
    for b in make_bars(cal, [(100, 100, 100, 100)] * 3, D1) + make_bars(cal, [(100, 100, 100, 100)] * 2, D2):
        engine.process(b)
    p = engine.portfolios["buy_hold_SPY"]
    qty = p.positions["SPY"].qty
    assert p.positions["SPY"].dividends == pytest.approx(qty * 2.0 * 0.85)
    pos = p.positions["SPY"]
    assert p.cash == pytest.approx(100_000 - qty * pos.entry_fill - pos.entry_fees + qty * 1.7)


def test_short_pays_full_dividend(cal, cfg):
    from botfolio.execution.portfolio import Portfolio, Position

    p = Portfolio("x", 1, cfg.risk, cfg.exits, None)  # type: ignore[arg-type]
    p.positions["SPY"] = Position("t", "SPY", -1, 10, None, 100, 100, 0, None, None, 0, None, False, None)  # type: ignore[arg-type]
    p.credit_dividend("SPY", 1.0, 15)
    assert p.cash == pytest.approx(100_000 - 10)


def test_trend_hold_follows_sma_with_band(cal):
    s = TrendHoldStrategy("trend_SPY", 1, {"sma_days": 3, "band_pct": 5.0}, ["SPY"])
    closes = [100, 100, 100, 100, 90, 90, 92, 120, 120]
    wants = [sig.side for b in _sessions(cal, closes) if (sig := s.on_bar(b)) is not None]
    # invested while warming up; 90 < 96.7*0.95 -> cash; 90 and 92 stay inside the band -> still cash; 120 -> buy
    assert wants == [1, 1, 1, 1, 0, 0, 0, 1]
    assert s.warmup_sessions == 8


def _closed_long(p, days, pnl_per_share, qty=100):
    """Open and close a 100-share long at $100 with zero costs, held ``days``."""
    from datetime import UTC, datetime

    from botfolio.execution.portfolio import Position
    from botfolio.strategies.base import Signal

    t0 = datetime(2025, 1, 2, 15, tzinfo=UTC)
    sig = Signal("x", "SPY", 1, t0, 100, None, hold=True)
    p.cash -= qty * 100
    p.positions["SPY"] = Position("t", "SPY", 1, qty, t0, 100, 100, 0, None, None, 0, None, True, sig)
    p._close(p.positions["SPY"], t0 + timedelta(days=days), 100 + pnl_per_share, "limit", "signal")


def test_australian_tax_discount_losses_and_fy_settlement(cfg):
    from botfolio.config import CostConfig, TaxConfig
    from botfolio.execution.costs import CostModel
    from botfolio.execution.portfolio import Portfolio

    zero = CostModel(CostConfig(half_spread_per_share=0, slippage_bps=0, sec_fee_per_million=0, finra_taf_per_share=0))
    p = Portfolio("x", 1, cfg.risk, cfg.exits, zero, TaxConfig(marginal_rate_pct=32))
    p.new_session(date(2025, 3, 3))
    _closed_long(p, 400, 10)  # $1,000 gain held > 12 months -> half taxable
    _closed_long(p, 30, 5)  # $500 short-term gain, fully taxable
    _closed_long(p, 30, -3)  # $300 loss, offsets the short-term gain first
    assert p.tax_due() == pytest.approx(0.32 * (200 + 500))
    assert p.equity() == pytest.approx(100_000 + 1_200 - 0.32 * 700)
    p.new_session(date(2025, 7, 1))  # new Australian financial year -> tax paid from cash
    assert p.tax_paid == pytest.approx(224) and p.cash == pytest.approx(100_000 + 1_200 - 224)
    _closed_long(p, 10, -20)  # $2,000 loss is carried forward, no refund
    p.new_session(date(2026, 7, 1))
    assert p.tax_paid == pytest.approx(224) and p.loss_carry == pytest.approx(2_000)
    p.credit_dividend("SPY", 0, 15)  # no position: ignored


def test_dividend_tax_tops_up_withholding(cfg):
    from botfolio.config import TaxConfig
    from botfolio.execution.portfolio import Portfolio, Position

    p = Portfolio("x", 1, cfg.risk, cfg.exits, None, TaxConfig(marginal_rate_pct=32))  # type: ignore[arg-type]
    p.positions["SPY"] = Position("t", "SPY", 1, 100, None, 100, 100, 0, None, None, 0, None, True, None)  # type: ignore[arg-type]
    p.credit_dividend("SPY", 1.0, 15)  # $100 gross, $15 withheld, Australian tax 32 -> 17 more
    assert p.cash == pytest.approx(100_000 + 85) and p.tax_due() == pytest.approx(17)
