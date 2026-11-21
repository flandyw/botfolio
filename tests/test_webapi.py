import asyncio
import json
from datetime import UTC, datetime

from botfolio.config import AppConfig
from botfolio.storage import Storage
from botfolio.webapi import create_app


def request(app, path, method="GET"):
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(app({
        "type": "http", "method": method, "path": path, "headers": [], "query_string": b"",
    }, receive, send))
    return messages[0]["status"], messages[1]["body"]


def test_paper_api_uses_worker_state_and_saved_watchlist(tmp_path):
    cfg = AppConfig(db_path=str(tmp_path / "test.sqlite"))
    storage = Storage(cfg.db_path)
    storage.put("heartbeat", datetime.now(UTC).isoformat())  # Legacy forward runner.
    app = create_app(cfg)

    assert json.loads(request(app, "/api/config")[1])["symbols"] == cfg.symbols
    assert json.loads(request(app, "/api/live")[1])["heartbeat"] is None
    now = datetime.now(UTC).isoformat()
    storage.put("event-watchlist", ["AAPL"])  # A list saved before the watchlist grew extends the default.
    assert json.loads(request(app, "/api/config")[1])["symbols"] == [*cfg.symbols, "AAPL"]
    storage.put("watchlist", ["AAPL"])
    storage.put("strategy-status", {"momentum": {"note": "2 breakouts"}})
    storage.put("event-heartbeat", now)
    storage.put("event-paper-armed", True)
    storage.put("event-execution-state", {
        "at": now, "phase": "ready", "enabled": True, "market_open": True, "open_intents": 2,
    })
    assert json.loads(request(app, "/api/config")[1])["symbols"] == ["AAPL"]
    status, body = request(app, "/api/live")
    assert status == 200
    state = json.loads(body)
    assert state["heartbeat"] == now
    assert state["phase"] == "ready"
    assert state["strategies"]["momentum"]["note"] == "2 breakouts"
    assert state["armed"] is True
    assert state["open_intents"] == 2
    assert request(app, "/api/live", "POST")[0] == 405
    storage.close()
