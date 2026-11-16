import numpy as np
import pandas as pd
import pytest

from ttbot.indicators import EMA, WilderATR, WilderRSI


def test_ema_matches_sma_seeded_reference():
    x = np.random.default_rng(0).normal(100, 1, 200)
    ema = EMA(10)
    vals = [ema.update(v) for v in x]
    assert all(v is None for v in vals[:9])
    ref = pd.Series(x).ewm(span=10, adjust=False).mean()  # pandas seeds with x[0]; compare after convergence
    seeded = [np.mean(x[:10])]
    for v in x[10:]:
        seeded.append(2 / 11 * v + 9 / 11 * seeded[-1])
    assert vals[9:] == pytest.approx(seeded)
    assert vals[-1] == pytest.approx(ref.iloc[-1], rel=1e-6)


def test_rsi_wilder_known_values():
    # classic Wilder example closes (from "New Concepts in Technical Trading Systems" style datasets)
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
              46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    rsi = WilderRSI(14)
    vals = [rsi.update(c) for c in closes]
    assert all(v is None for v in vals[:14])
    assert vals[14] == pytest.approx(70.46, abs=0.05)
    assert vals[15] == pytest.approx(66.25, abs=0.05)


def test_rsi_extremes():
    rsi = WilderRSI(3)
    for c in range(1, 10):
        v = rsi.update(float(c))
    assert v == 100.0


def test_atr_seed_and_smoothing():
    atr = WilderATR(3)
    bars = [(10, 9, 9.5), (11, 9.5, 10.5), (10.8, 10, 10.2), (12, 10.1, 11.9)]
    vals = [atr.update(*b) for b in bars]
    trs = [1.0, 1.5, 0.8]
    assert vals[2] == pytest.approx(sum(trs) / 3)
    tr4 = max(12 - 10.1, abs(12 - 10.2), abs(10.1 - 10.2))
    assert vals[3] == pytest.approx((vals[2] * 2 + tr4) / 3)
