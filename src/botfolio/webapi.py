"""Read-only API for the paper worker's watchlist and execution status."""

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
        return JSONResponse({"symbols": watchlist(storage, cfg), "feed": cfg.data.feed})

    async def live(_):
        heartbeat = storage.get("event-heartbeat")
        state = storage.get("event-execution-state") or {}
        now = datetime.now(UTC)
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
        })

    routes = [Route("/api/config", config), Route("/api/live", live)]
    if WEB_DIST.is_dir():
        routes.append(Mount("/", StaticFiles(directory=WEB_DIST, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
