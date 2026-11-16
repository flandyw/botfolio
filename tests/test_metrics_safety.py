import pandas as pd
import pytest

from ttbot.metrics import benjamini_hochberg, max_drawdown, sharpe, significance
from ttbot.safety import LiveTradingForbidden, assert_paper_account, assert_paper_url, check_environment


def test_max_drawdown():
    assert max_drawdown(pd.Series([100, 120, 90, 130, 117])) == pytest.approx(25.0)


def test_sharpe_sign_and_zero():
    assert sharpe(pd.Series([0.01, 0.02, -0.005, 0.01])) > 0
    assert sharpe(pd.Series([0.0, 0.0, 0.0])) == 0.0


def test_benjamini_hochberg():
    q = benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    assert q["a"] == pytest.approx(0.04) and q["b"] == pytest.approx(0.0533333, rel=1e-4)
    assert q["c"] == pytest.approx(0.0533333, rel=1e-4) and q["d"] == pytest.approx(0.5)


def test_significance_detects_clear_edge():
    import numpy as np

    r = np.random.default_rng(0).normal(0.5, 1, 400)
    s = significance(r)
    assert s["p_ttest"] < 1e-6 and s["ci_low"] > 0


def test_live_urls_rejected():
    assert_paper_url("https://paper-api.alpaca.markets/v2/orders")
    for bad in ("https://api.alpaca.markets/v2/orders", "https://evil.example/v2/orders", "http://localhost"):
        with pytest.raises(LiveTradingForbidden):
            assert_paper_url(bad)


def test_live_account_rejected():
    assert_paper_account({"account_number": "PA12345"})
    with pytest.raises(LiveTradingForbidden):
        assert_paper_account({"account_number": "123456789"})
    with pytest.raises(LiveTradingForbidden):
        assert_paper_account({})


def test_environment_guard(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(LiveTradingForbidden):
        check_environment()
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    check_environment()
    monkeypatch.setenv("ALPACA_PAPER", "false")
    with pytest.raises(LiveTradingForbidden):
        check_environment()


def test_order_submission_blocked_for_live_account(monkeypatch):
    from ttbot.config import Credentials
    from ttbot.data.alpaca import AlpacaClient

    client = AlpacaClient(Credentials(key_id="AK_LIVE", secret_key="x"))
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url))
        return {"account_number": "987654321", "status": "ACTIVE"}  # a live account

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(LiveTradingForbidden):
        client.submit_paper_order("SPY", 1, "buy", "t1")
    assert all(m == "GET" for m, _ in calls), "no order may be POSTed"
