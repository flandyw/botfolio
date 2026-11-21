import pytest

from botfolio.safety import LiveTradingForbidden, assert_paper_account, assert_paper_url, check_environment


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
    from botfolio.config import Credentials
    from botfolio.data.alpaca import AlpacaClient
    from botfolio.events.paper import PaperExecutor
    from botfolio.events.store import EventStore
    from botfolio.storage import Storage

    client = AlpacaClient(Credentials(key_id="AK_LIVE", secret_key="x"))
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url))
        return {"account_number": "987654321", "status": "ACTIVE"}  # a live account

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(LiveTradingForbidden):
        PaperExecutor(client, EventStore(Storage(":memory:"))).request("POST", "orders")
    assert all(m == "GET" for m, _ in calls), "no order may be POSTed"
