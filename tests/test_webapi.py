import asyncio
import json
from datetime import UTC, datetime, timedelta

import pandas as pd
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
    cfg = AppConfig(db_path=str(tmp_path / "botfolio.sqlite"), symbols=load_config().symbols, etfs=load_config().etfs,
                    strategies=load_config().strategies)
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
    storage.put("watchlist", ["AAPL", "VOO"])
    storage.put("strategy-status", {"momentum": {"note": "2 breakouts"}})
    storage.put("event-heartbeat", now)
    storage.put("event-paper-armed", True)
    check_in(storage, enabled=True, market_open=True, open_intents=2)
    config = request(api, "/api/config")[1]
    assert (config["symbols"], config["etfs"]) == (["AAPL", "VOO"], ["VOO"])
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
    assert state["strategy_config"]["momentum"]["symbols"] == ["SPY", "QQQ"]
    assert state["intents"] == [
        {"symbol": "SPY", "strategy": "etf_trend", "side": "long", "qty": 1.5, "state": "submitted", "opened": now}]
    assert [(row["key"], row["strategy"]) for row in request(api, "/api/orders")[1]] == [("a", "etf_trend"), ("b", "news")]
    assert request(api, "/api/live", "POST")[0] == 405


@pytest.mark.parametrize("exit_type", ["stop", "limit", "strategy"])
def test_order_detail_uses_broker_fills_and_preserves_the_strategy_setup(api, monkeypatch, exit_type):
    key = "orb-SPY-2026-11-20"
    entry_at, exit_at = "2026-11-20T15:00:04Z", "2026-11-20T15:05:03Z"
    setup = {"at": "2026-11-20T15:00:00+00:00", "price": 499.5, "levels": {"Opening high": 503}}
    row = {"state": "closed", "symbol": "SPY", "strategy": "orb", "side": "short", "client_id": "evt-" + key,
           "opened": "2026-11-20T15:00:01+00:00", "limit": 500, "stop": 503, "target": 494, "context": setup,
           "exit_reserved": exit_type == "strategy"}
    with api.storage.tx() as conn:
        conn.execute("INSERT INTO event_orders VALUES (?,?)", (key, json.dumps(row)))

    class Broker:
        def get_paper_account(self):
            return {"account_number": "PA123"}

        def _request(self, method, url, params):
            assert method == "GET" and url.startswith("https://paper-api.alpaca.markets/v2/")
            exit_fill = {"status": "filled", "filled_avg_price": "498.25", "filled_qty": "2", "filled_at": exit_at}
            parent = {"id": "entry", "status": "filled", "filled_avg_price": "500.15", "filled_qty": "2",
                      "filled_at": entry_at, "order_class": "bracket"}
            if url.endswith("/orders/entry"):
                assert params == {"nested": "true"}
                return {**parent, "legs": [{"type": exit_type, **exit_fill}] if exit_type != "strategy" else []}
            if params["client_order_id"] == "exit-" + key:
                assert exit_type == "strategy"
                return exit_fill
            assert params == {"client_order_id": "evt-" + key}
            return parent

        def get_bars(self, symbols, start, end, timeframe, feed, adjustment):
            assert symbols == ["SPY"] and start < datetime.fromisoformat(entry_at) < end
            assert timeframe == "5Min" and adjustment == "raw"
            return pd.DataFrame([{"ts": pd.Timestamp("2026-11-20T15:00:00Z"),
                                  "open": 500, "high": 501, "low": 499, "close": 500, "volume": 100}])

    monkeypatch.setattr("botfolio.webapi.load_credentials", lambda **_: object())
    monkeypatch.setattr("botfolio.webapi.AlpacaClient", lambda *_, **__: Broker())
    status, detail = request(api, "/api/orders/detail", query=f"key={key}")
    assert status == 200
    assert detail["order"] == {"key": key, **row}
    assert detail["context"]["levels"] == setup["levels"]
    assert detail["context"]["time"] == detail["bars"][0]["time"]
    assert detail["entry"]["price"] == 500.15 and detail["entry"]["qty"] == 2
    assert detail["entry"]["time"] == detail["bars"][0]["time"] + 4
    assert [(f["kind"], f["price"], f["at"]) for f in detail["exits"]] == [
        ({"stop": "Stop loss", "limit": "Take profit", "strategy": "Strategy exit"}[exit_type], 498.25, exit_at)]


def test_order_detail_keeps_legacy_journal_data_without_broker_credentials(api, monkeypatch):
    monkeypatch.setattr("botfolio.webapi.load_credentials", lambda **_: None)
    with api.storage.tx() as conn:
        conn.execute("INSERT INTO event_orders VALUES (?,?)",
                     ("old", json.dumps({"state": "closed", "symbol": "SPY", "stop": 500})))
    assert request(api, "/api/orders/detail", query="key=missing")[0] == 404
    status, detail = request(api, "/api/orders/detail", query="key=old")
    assert status == 200
    assert detail["order"]["stop"] == 500 and detail["order"]["strategy"] == "news"
    assert detail["entry"] is None and detail["exits"] == [] and detail["bars"] == [] and detail["error"]


def test_order_detail_reuses_server_cache_until_the_journal_changes(api, monkeypatch):
    calls = []

    def activity(_, row, feed, context):
        calls.append(row["state"])
        return dict(entry=None, exits=[], bars=[], bar_seconds=300, context=context)

    monkeypatch.setattr("botfolio.webapi.load_credentials", lambda **_: None)
    monkeypatch.setattr("botfolio.webapi.trade_activity", activity)
    row = {"state": "submitted", "symbol": "SPY"}
    with api.storage.tx() as conn:
        conn.execute("INSERT INTO event_orders VALUES (?,?)", ("cached", json.dumps(row)))
    query = "key=cached"
    first = request(api, "/api/orders/detail", query=query)
    assert first == request(api, "/api/orders/detail", query=query)
    assert calls == ["submitted"]
    with api.storage.tx() as conn:
        conn.execute("UPDATE event_orders SET payload=? WHERE event_key=?",
                     (json.dumps({**row, "state": "closed"}), "cached"))
    assert request(api, "/api/orders/detail", query=query)[1]["order"]["state"] == "closed"
    assert calls == ["submitted", "closed"]


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


def test_desk_benchmark_settings_defaults_validation_and_save(api, monkeypatch):
    assert request(api, "/api/config")[1]["benchmarks"] == {"purchase_date": None, "annual_rate_pct": 5.75}
    options = {"purchase_date": "2024-11-16", "annual_rate_pct": 4.25}
    path = "/api/desk-benchmarks"
    assert request(api, path, "PUT", options)[0] == 403
    monkeypatch.setenv("BOTFOLIO_DASHBOARD_PASSWORD", "test-only-password")
    assert request(api, path, "PUT", options, WRITE)[0] == 401
    headers = {**WRITE, "x-botfolio-password": "test-only-password"}
    assert request(api, path, "PUT", options, headers) == (200, options)
    for bad in ({"purchase_date": "2024-02-30"}, {"purchase_date": "9999-01-01"}, {"annual_rate_pct": -1}):
        assert request(api, path, "PUT", {**options, **bad}, headers)[0] == 400
    assert request(api, "/api/config")[1]["benchmarks"] == options


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
    assert request(api, "/api/orders/detail", query="key=missing")[0] == 401
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


def test_ai_settings_validated_and_reported(api, monkeypatch):
    monkeypatch.setenv("EVENT_AI_PROVIDER", "clairic")
    monkeypatch.setenv("EVENT_AI_MODEL", "clairic")
    monkeypatch.setenv("EVENT_AI_URL", "https://cf.example/ai/run/@cf/cloudflare/clairic")
    monkeypatch.setenv("EVENT_AI_KEY", "k")
    monkeypatch.setenv("EVENT_AI_CLAUDE_MODEL", "sonnet")
    for name in ("CHATGPT", "VERCEL"):
        monkeypatch.delenv(f"EVENT_AI_{name}_MODEL", raising=False)
    status, body = request(api, "/api/ai")
    assert body["settings"]["order"] == ["claude", "chatgpt", "vercel", "cloudflare"]
    assert [p["name"] for p in body["providers"] if p["ready"]] == ["claude", "cloudflare"]
    assert body["budget"]["plans"]["claude"]["five_hour"] == 0 and body["days"] == []
    assert request(api, "/api/ai", "PUT", {"order": ["cloudflare", "cloudflare"]}, WRITE)[0] == 400
    assert request(api, "/api/ai", "PUT", {"order": ["nope"]}, WRITE)[0] == 400
    assert request(api, "/api/ai", "PUT", {"models": {"cloudflare": "gpt"}}, WRITE)[0] == 400
    assert request(api, "/api/ai", "PUT", {"models": {"vercel": "x"}}, WRITE)[0] == 400  # not configured
    assert request(api, "/api/ai", "PUT", {"order": ["cloudflare"]})[0] == 403
    status, body = request(api, "/api/ai", "PUT", {"order": ["cloudflare", "claude"],
                                                   "models": {"cloudflare": "clairic-flash", "claude": "haiku"}}, WRITE)
    assert status == 200 and body["settings"]["order"] == ["cloudflare", "claude"]
    assert body["settings"]["models"] == {"claude": "haiku", "cloudflare": "clairic-flash"}
    assert request(api, "/api/ai", "PUT", {"monthly_usd": 500}, WRITE)[0] == 400
    status, body = request(api, "/api/ai", "PUT", {"monthly_usd": 2.5}, WRITE)
    assert body["settings"]["monthly_usd"] == 2.5 and body["budget"]["monthly_usd"] == 2.5
