"""Read-only API for the paper worker's watchlist and execution status."""

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from botfolio.config import AppConfig, watchlist
from botfolio.storage import Storage

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(cfg: AppConfig) -> Starlette:
    storage = Storage(cfg.db_path)

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            storage.close()

    async def config(_):
        symbols = watchlist(storage, cfg)
        return JSONResponse({"symbols": symbols, "etfs": [s for s in symbols if s in set(cfg.etfs)],
                             "feed": cfg.data.feed})

    def intents():
        try:
            with storage.tx() as conn:
                rows = [json.loads(payload) for (payload,) in conn.execute("SELECT payload FROM event_orders")]
        except sqlite3.OperationalError:  # the worker has not created its journal yet
            return []
        keep = ("symbol", "strategy", "side", "qty", "state", "opened", "exit_requested")
        return [{"strategy": "news", **{k: row.get(k) for k in keep if row.get(k) is not None}}
                for row in rows if row.get("state") != "closed"]

    async def live(_):
        heartbeat = storage.get("event-heartbeat")
        state = storage.get("event-execution-state") or {}
        now = datetime.now(UTC)
        limits = cfg.limits()
        return JSONResponse({
            "heartbeat": heartbeat,
            "heartbeat_age_s": (now - datetime.fromisoformat(heartbeat)).total_seconds() if heartbeat else None,
            "execution_age_s": (now - datetime.fromisoformat(state["at"])).total_seconds() if state.get("at") else None,
            "market_open": state.get("market_open"),
            "phase": state.get("phase"),
            "enabled": state.get("enabled", False),
            "armed": bool(storage.get("event-paper-armed")),
            "open_intents": state.get("open_intents"),
            "strategies": storage.get("strategy-status") or {},
            "strategy_config": {
                "news": {"enabled": cfg.strategies.news.enabled, **limits["news"]},
                "momentum": {"enabled": cfg.strategies.momentum.enabled, **limits["momentum"]},
                "etf_trend": {"enabled": cfg.strategies.etf_trend.enabled, **limits["etf_trend"],
                              "allocation_pct": cfg.strategies.etf_trend.allocation_pct,
                              "sma_days": cfg.strategies.etf_trend.sma_days},
            },
            "news_check": storage.get("event-last-entry-check"),
            "intents": intents(),
        })

    routes = [Route("/api/config", config), Route("/api/live", live)]
    if WEB_DIST.is_dir():
        routes.append(Mount("/", StaticFiles(directory=WEB_DIST, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
