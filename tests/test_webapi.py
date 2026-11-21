import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from botfolio.config import AppConfig, load_config
from botfolio.events.ai import AI, analyze
from botfolio.events.models import Event
from botfolio.events.store import EventStore
from botfolio.storage import Storage
from botfolio.webapi import create_app

WRITE = {"x-botfolio": "1"}


def request(app, path, method="GET", body=None, headers=None, query=""):
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"",
                "more_body": False}

    asyncio.run(app({
        "type": "http", "method": method, "path": path, "query_string": query.encode(),
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }, receive, send))
    raw = messages[1]["body"]
    return messages[0]["status"], json.loads(raw) if raw[:1] in (b"{", b"[") else raw


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("BOTFOLIO_DASHBOARD_PASSWORD", "")
    monkeypatch.setenv("EVENT_AI_PROVIDER", "disabled")
    monkeypatch.setenv("EVENT_AI_KEY", "")
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path / "KILL"))
    cfg = AppConfig(db_path=str(tmp_path / "botfolio.sqlite"), symbols=load_config().symbols, etfs=load_config().etfs)
    app, storage = create_app(cfg), Storage(cfg.db_path)
    app.cfg, app.storage, app.kill = cfg, storage, tmp_path / "KILL"
    yield app
    storage.close()


def check_in(storage, **state):
    storage.put("event-execution-state", {"at": datetime.now(UTC).isoformat(), "phase": "ready", **state})


def test_paper_api_uses_worker_state_and_saved_watchlist(api):
    cfg, storage = api.cfg, api.storage
    assert len(cfg.symbols) > 100 and set(cfg.etfs) <= set(cfg.symbols)
    assert request(api, "/api/config")[1]["symbols"] == cfg.symbols
    assert request(api, "/api/live")[1]["heartbeat"] is None
    now = datetime.now(UTC).isoformat()
    storage.put("event-watchlist", ["ZZZ"])  # A list saved before the watchlist grew extends the default.
    assert request(api, "/api/config")[1]["symbols"] == [*cfg.symbols, "ZZZ"]
    storage.put("watchlist", ["AAPL", "SPY"])
    storage.put("strategy-status", {"momentum": {"note": "2 breakouts"}})
    storage.put("event-heartbeat", now)
    storage.put("event-paper-armed", True)
    check_in(storage, enabled=True, market_open=True, open_intents=2)
    config = request(api, "/api/config")[1]
    assert (config["symbols"], config["etfs"]) == (["AAPL", "SPY"], ["SPY"])
    with storage.tx() as conn:
        conn.execute("INSERT INTO event_orders VALUES (?,?)", ("a", json.dumps(
            {"state": "submitted", "symbol": "SPY", "strategy": "etf_trend", "side": "long", "qty": 1.5,
             "opened": now})))
        conn.execute("INSERT INTO event_orders VALUES (?,?)", ("b", json.dumps({"state": "closed", "symbol": "QQQ"})))
    status, state = request(api, "/api/live")
    assert status == 200
    assert (state["heartbeat"], state["phase"], state["armed"], state["open_intents"]) == (now, "ready", True, 2)
    assert state["strategies"]["momentum"]["note"] == "2 breakouts"
    assert state["strategy_config"]["etf_trend"]["enabled"] is True and state["worker_ready"] is True
    assert state["intents"] == [
        {"symbol": "SPY", "strategy": "etf_trend", "side": "long", "qty": 1.5, "state": "submitted", "opened": now}]
    assert [(row["key"], row["strategy"]) for row in request(api, "/api/orders")[1]] == [("a", "etf_trend"), ("b", "news")]
    assert request(api, "/api/live", "POST")[0] == 405


def test_entries_are_off_until_every_arming_condition_holds(api, monkeypatch):
    storage = api.storage
    arm = lambda body={"dedicated": True}: request(api, "/api/control/arm", "POST", body, WRITE)  # noqa: E731
    status, answer = arm()
    assert status == 409 and "worker has not checked in" in answer["error"]
    assert not storage.get("event-paper-armed")
    check_in(storage)
    assert arm({"dedicated": False})[0] == 409 and arm({})[0] == 409
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "false")
    assert "not switched on" in arm()[1]["error"]
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    # No AI provider is configured: momentum and ETF trend do not need one, so arming still works.
    assert request(api, "/api/live")[1]["ai_ready"] is False
    assert arm() == (200, {"armed": True, "stopped": False})
    assert request(api, "/api/control/pause", "POST", headers=WRITE) == (200, {"armed": False, "stopped": False})
    assert request(api, "/api/control/unknown", "POST", headers=WRITE)[0] == 404


def test_emergency_stop_pauses_entries_and_must_be_cleared_before_arming(api):
    storage = api.storage
    check_in(storage)
    storage.put("event-paper-armed", True)
    assert request(api, "/api/control/stop", "POST", headers=WRITE) == (200, {"armed": False, "stopped": True})
    assert api.kill.exists() and request(api, "/api/live")[1]["stopped"] is True
    assert "emergency stop" in request(api, "/api/control/arm", "POST", {"dedicated": True}, WRITE)[1]["error"]
    assert request(api, "/api/control/clear-stop", "POST", headers=WRITE) == (200, {"armed": False, "stopped": False})
    assert not api.kill.exists() and storage.get("event-paper-armed") is False


def test_watchlist_validation_and_save(api):
    storage = api.storage
    save = lambda symbols: request(api, "/api/watchlist", "PUT", {"symbols": symbols}, WRITE)  # noqa: E731
    assert save(["AAPL", " msft ", "aapl"]) == (200, {"symbols": ["AAPL", "MSFT"]})
    assert storage.get("watchlist") == ["AAPL", "MSFT"]
    for bad in (["bad/ticker"], [], [f"T{i}" for i in range(501)], "AAPL"):
        assert save(bad)[0] == 400
    assert request(api, "/api/watchlist", "PUT", headers=WRITE)[0] == 400
    assert storage.get("watchlist") == ["AAPL", "MSFT"]
    large = [f"T{i}" for i in range(150)]
    assert save(large)[0] == 200 and storage.get("watchlist") == large


def test_writes_need_the_custom_header_and_the_password_when_one_is_set(api, monkeypatch):
    storage = api.storage
    storage.put("event-paper-armed", True)
    assert request(api, "/api/control/pause", "POST")[0] == 403  # a form on another site cannot send the header
    assert storage.get("event-paper-armed") is True
    monkeypatch.setenv("BOTFOLIO_DASHBOARD_PASSWORD", "test-only-password")
    assert request(api, "/api/config")[1]["password_required"] is True
    assert request(api, "/api/control/pause", "POST", headers=WRITE)[0] == 401
    assert request(api, "/api/control/pause", "POST", headers={**WRITE, "x-botfolio-password": "wrong"})[0] == 401
    assert request(api, "/api/watchlist", "PUT", {"symbols": ["AAPL"]}, WRITE)[0] == 401
    assert request(api, "/api/news")[0] == 401 and request(api, "/api/orders")[0] == 401
    assert storage.get("event-paper-armed") is True and storage.get("watchlist") is None
    signed_in = {**WRITE, "x-botfolio-password": "test-only-password"}
    assert request(api, "/api/control/pause", "POST", headers=signed_in)[0] == 200
    assert request(api, "/api/news", headers=signed_in)[0] == 200
    assert request(api, "/api/live")[0] == 200  # status stays readable, as before


def test_news_lists_saved_articles_with_the_latest_assessment(api):
    store = EventStore(api.storage)
    published = datetime.now(UTC) - timedelta(days=10)
    events = [Event(source="fixture", source_id=f"ui-{ticker}", ticker=ticker, event_type="news",
                    published_at=published, content_at=published + timedelta(minutes=i),
                    received_at=datetime.now(UTC), source_url="https://example.com/news",
                    text="Original fixture article. " * 40) for i, ticker in enumerate(("SPY", "QQQ"))]
    for event in events:
        store.add(event)
    analyze(store, events[0], "ui-disabled", AI(), {})
    status, news = request(api, "/api/news")
    assert status == 200 and news["tickers"] == ["QQQ", "SPY"] and news["total"] == 2 and news["archived"] is True
    qqq, spy = news["items"]
    assert (qqq["ticker"], qqq["decision"]) == ("QQQ", None)
    assert spy["decision"]["action"] == "WAIT" and spy["decision"]["experiment"] == "ui-disabled"
    assert len(spy["text"]) == 600 and spy["truncated"] is True
    assert [item["ticker"] for item in request(api, "/api/news", query="ticker=spy")[1]["items"]] == ["SPY"]
