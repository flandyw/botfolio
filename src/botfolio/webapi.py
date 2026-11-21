"""API behind the web dashboard: worker status, the order journal, the news audit and the paper controls."""

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from botfolio.config import AppConfig, watchlist
from botfolio.events.paper import configured
from botfolio.events.store import EventStore
from botfolio.storage import Storage

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
MAX_WATCHLIST = 500
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}")


def kill_file() -> Path:
    return Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL"))


def create_app(cfg: AppConfig) -> Starlette:
    storage = Storage(cfg.db_path)
    store = EventStore(storage)

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            storage.close()

    def denied(request, write=False):
        """Controls and the audit need the dashboard password when one is set. Writes also need a custom
        header, which a page on another site cannot send, so a browser cannot be tricked into trading."""
        if write and request.headers.get("x-botfolio") != "1":
            return JSONResponse({"error": "Missing X-Botfolio header."}, 403)
        password = os.getenv("BOTFOLIO_DASHBOARD_PASSWORD", "")
        given = request.headers.get("x-botfolio-password", "")
        if password and not hmac.compare_digest(given.encode(), password.encode()):
            return JSONResponse({"error": "Enter the dashboard password."}, 401)
        return None

    def journal():
        with storage.tx() as conn:
            return [{"key": key, "strategy": "news", **json.loads(payload)}
                    for key, payload in conn.execute("SELECT event_key,payload FROM event_orders")]

    def worker_ready(now):
        state = storage.get("event-execution-state") or {}
        return bool(state.get("at") and 0 <= (now - datetime.fromisoformat(state["at"])).total_seconds() <= 20
                    and state.get("phase") != "error")

    async def config(_):
        symbols = watchlist(storage, cfg)
        return JSONResponse({"symbols": symbols, "etfs": [s for s in symbols if s in set(cfg.etfs)],
                             "feed": cfg.data.feed, "max_symbols": MAX_WATCHLIST,
                             "password_required": bool(os.getenv("BOTFOLIO_DASHBOARD_PASSWORD", ""))})

    async def live(_):
        heartbeat = storage.get("event-heartbeat")
        state = storage.get("event-execution-state") or {}
        data = storage.get("event-data-status") or {}
        now = datetime.now(UTC)
        limits = cfg.limits()
        latest_news = data.get("latest_content")
        keep = ("symbol", "strategy", "side", "qty", "state", "opened", "exit_requested")
        return JSONResponse({
            "heartbeat": heartbeat,
            "heartbeat_age_s": (now - datetime.fromisoformat(heartbeat)).total_seconds() if heartbeat else None,
            "execution_age_s": (now - datetime.fromisoformat(state["at"])).total_seconds() if state.get("at") else None,
            "market_open": state.get("market_open"),
            "phase": state.get("phase"),
            "message": state.get("message"),
            "enabled": configured(),
            "armed": bool(storage.get("event-paper-armed")),
            "stopped": kill_file().exists(),
            "worker_ready": worker_ready(now),
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
            "news_fresh": bool(latest_news and now - datetime.fromisoformat(latest_news) <= timedelta(minutes=5)),
            "provider_failures": bool(data.get("provider_failures")),
            "issuer_feed_failures": bool(data.get("issuer_feed_failures")),
            "ai_ready": os.getenv("EVENT_AI_PROVIDER", "disabled") != "disabled" and bool(os.getenv("EVENT_AI_KEY")),
            "intents": [{k: row[k] for k in keep if row.get(k) is not None}
                        for row in journal() if row.get("state") != "closed"],
        })

    async def orders(request):
        """Every order the worker has recorded, newest first."""
        return denied(request) or JSONResponse(
            sorted(journal(), key=lambda row: row.get("opened") or "", reverse=True)[:200])

    async def news(request):
        """Saved news, newest first, each with the latest AI assessment."""
        if refused := denied(request):
            return refused
        ticker = request.query_params.get("ticker", "").upper()
        events = store.events()
        tickers = sorted({event.ticker for event in events})
        events = sorted((e for e in events if not ticker or e.ticker == ticker), key=lambda e: e.content_at, reverse=True)
        audit = store.audit().sort_values("completed_at").drop_duplicates("event_key", keep="last").set_index("event_key")
        items = []
        for event in events[:50]:
            record = audit.loc[event.key] if event.key in audit.index else None
            decision = json.loads(record.payload) if record is not None else None
            items.append({
                "key": event.key, "ticker": event.ticker, "source": event.source, "url": event.source_url,
                "published_at": event.published_at.isoformat(), "received_at": event.received_at.isoformat(),
                "text": event.text[:600], "truncated": len(event.text) > 600,
                "decision": decision and {
                    "action": decision["recommended_action"], "reasoning": decision["reasoning"],
                    "evidence": decision["key_evidence"], "risks": decision["risk_factors"],
                    "model": record.model, "experiment": record.experiment},
            })
        archived = bool(events) and datetime.now(UTC) - events[0].content_at > timedelta(days=1)
        return JSONResponse({"tickers": tickers, "total": len(events), "archived": archived, "items": items})

    async def save_watchlist(request):
        if refused := denied(request, write=True):
            return refused
        try:
            given = (await request.json())["symbols"]
            if not isinstance(given, list):
                raise TypeError
            symbols = list(dict.fromkeys(str(part).strip().upper() for part in given if str(part).strip()))
        except (ValueError, KeyError, TypeError):
            symbols = []
        if not symbols or len(symbols) > MAX_WATCHLIST or any(not SYMBOL.fullmatch(s) for s in symbols):
            return JSONResponse({"error": f"Enter 1–{MAX_WATCHLIST} valid ticker symbols separated by commas."}, 400)
        storage.put("watchlist", symbols)
        return JSONResponse({"symbols": symbols})

    async def control(request):
        """arm, pause, stop (emergency stop) or clear-stop. Entries are never armed as a side effect."""
        if refused := denied(request, write=True):
            return refused
        action = request.path_params["action"]
        if action == "arm":
            try:
                dedicated = (await request.json()).get("dedicated") is True
            except (ValueError, AttributeError):
                dedicated = False
            missing = [label for label, ok in (
                ("paper execution is not switched on in .env", configured()),
                ("the worker has not checked in within the last 20 seconds", worker_ready(datetime.now(UTC))),
                ("the emergency stop is active", not kill_file().exists()),
                ("the dedicated paper account is not confirmed", dedicated)) if not ok]
            if missing:
                return JSONResponse({"error": "Cannot enable entries: " + "; ".join(missing) + "."}, 409)
            storage.put("event-paper-armed", True)
        elif action in ("pause", "stop", "clear-stop"):
            storage.put("event-paper-armed", False)
            if action == "stop":
                kill_file().parent.mkdir(parents=True, exist_ok=True)
                kill_file().touch()
            elif action == "clear-stop":
                kill_file().unlink(missing_ok=True)
        else:
            return JSONResponse({"error": "Unknown action."}, 404)
        return JSONResponse({"armed": bool(storage.get("event-paper-armed")), "stopped": kill_file().exists()})

    routes = [Route("/api/config", config), Route("/api/live", live), Route("/api/orders", orders),
              Route("/api/news", news), Route("/api/watchlist", save_watchlist, methods=["PUT"]),
              Route("/api/control/{action}", control, methods=["POST"])]
    if WEB_DIST.is_dir():
        routes.append(Mount("/", StaticFiles(directory=WEB_DIST, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
