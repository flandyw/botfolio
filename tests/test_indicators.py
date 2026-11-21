import pytest

from botfolio.indicators import WilderATR


def test_atr_seed_and_smoothing():
    atr = WilderATR(3)
    bars = [(10, 9, 9.5), (11, 9.5, 10.5), (10.8, 10, 10.2), (12, 10.1, 11.9)]
    vals = [atr.update(*b) for b in bars]
    trs = [1.0, 1.5, 0.8]
    assert vals[2] == pytest.approx(sum(trs) / 3)
    tr4 = max(12 - 10.1, abs(12 - 10.2), abs(10.1 - 10.2))
    assert vals[3] == pytest.approx((vals[2] * 2 + tr4) / 3)
