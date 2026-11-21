"""API behind the web dashboard: worker status, the order journal, the news audit and the paper controls."""

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from botfolio.config import AppConfig, watchlist
from botfolio.events.ai import CLOUDFLARE_MODELS, FREE_NEURONS, budget, neurons_per_million
from botfolio.events.paper import configured
from botfolio.events.store import EventStore
from botfolio.storage import Storage

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
MAX_WATCHLIST = 500
NEURON_USD = 0.011 / 1000
MAX_MONTHLY_USD = 50  # slider ceiling; a typo should not spend real money
PROVIDER_FAILURE = "Provider failure or invalid structured output"
_vercel_models: dict = {}
_vercel_credits: dict = {}


def vercel_credits() -> float | None:
    """Remaining gateway credit in USD from /v1/credits, cached for five minutes; None when unknown."""
    key = os.getenv("EVENT_AI_SECONDARY_KEY", "")
    if key and _vercel_credits.get("at", 0) < datetime.now(UTC).timestamp() - 300:
        try:
            balance = requests.get("https://ai-gateway.vercel.sh/v1/credits", timeout=10,
                                   headers={"Authorization": "Bearer " + key}).json()["balance"]
            _vercel_credits.update(at=datetime.now(UTC).timestamp(), balance=float(balance))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            pass
    return _vercel_credits.get("balance")


def vercel_models() -> list[str]:
    """Gateway language models that accept a JSON response format, cached for an hour."""
    if _vercel_models.get("at", 0) < datetime.now(UTC).timestamp() - 3600:
        try:
            data = requests.get("https://ai-gateway.vercel.sh/v1/models", timeout=10).json()["data"]
            _vercel_models.update(at=datetime.now(UTC).timestamp(), ids=sorted(
                m["id"] for m in data if m.get("type") == "language"
                and "response_format" in m.get("supported_parameters", [])))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return _vercel_models.get("ids", [])
    return _vercel_models["ids"]


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
                "etf_rotation": {"enabled": cfg.strategies.etf_rotation.enabled, **limits["etf_rotation"],
                                 "allocation_pct": cfg.strategies.etf_rotation.allocation_pct},
                "cash_park": {"enabled": cfg.strategies.cash_park.enabled, **limits["cash_park"],
                              "symbol": cfg.strategies.cash_park.symbol},
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

    def matching(params, audit):
        """Events passing the news page's filters (ticker, status, action, source, text, age), plus all tickers/sources."""
        ticker, status = params.get("ticker", "").upper(), params.get("status", "")
        action, source, text = params.get("action", ""), params.get("source", ""), params.get("q", "").lower().strip()
        try:
            since = datetime.now(UTC) - timedelta(hours=float(params["hours"])) if params.get("hours") else None
        except ValueError:
            since = None
        everything = store.events()
        actions = {k: json.loads(p)["recommended_action"] for k, p in zip(audit.index, audit.payload, strict=True)}

        def keep(e):
            return ((not ticker or e.ticker == ticker) and (not source or e.source.split(":")[0] == source)
                    and (status != "assessed" or e.key in actions) and (status != "unassessed" or e.key not in actions)
                    and (not action or actions.get(e.key) == action) and (not text or text in e.text.lower())
                    and (not since or e.published_at >= since))
        return [e for e in everything if keep(e)], everything

    async def news(request):
        """Saved news with the latest AI assessment; filtered and sorted by the query string."""
        if refused := denied(request):
            return refused
        q = request.query_params
        with storage.tx() as conn:
            queued = [r[0].removeprefix("news-assess:") for r in conn.execute(
                "SELECT key FROM kv WHERE key LIKE 'news-assess:%'")]
        audit = store.audit().sort_values("completed_at").drop_duplicates("event_key", keep="last").set_index("event_key")
        events, everything = matching(q, audit)
        assessed_at = {k: t for k, t in zip(audit.index, audit.completed_at, strict=True)}
        sort = q.get("sort", "published")
        events.sort(key={"saved": lambda e: e.received_at, "ticker": lambda e: e.ticker,
                         "assessed": lambda e: assessed_at.get(e.key, "")}.get(sort, lambda e: e.content_at),
                    reverse=sort != "ticker")
        try:
            limit = min(max(int(q.get("limit", 50)), 1), 500)
        except ValueError:
            limit = 50
        items = []
        for event in events[:limit]:
            record = audit.loc[event.key] if event.key in audit.index else None
            decision = json.loads(record.payload) if record is not None else None
            items.append({
                "key": event.key, "ticker": event.ticker, "source": event.source, "url": event.source_url,
                "published_at": event.published_at.isoformat(), "received_at": event.received_at.isoformat(),
                "text": event.text[:600], "truncated": len(event.text) > 600,
                "decision": decision and {
                    "action": decision["recommended_action"], "reasoning": decision["reasoning"],
                    "evidence": decision["key_evidence"], "risks": decision["risk_factors"],
                    "model": record.model, "experiment": record.experiment, "at": record.completed_at},
            })
        unassessed = sum(e.key not in audit.index for e in events)
        newest = max((e.content_at for e in everything), default=None)
        archived = newest is not None and datetime.now(UTC) - newest > timedelta(days=1)
        return JSONResponse({"tickers": sorted({e.ticker for e in everything}),
                             "sources": sorted({e.source.split(":")[0] for e in everything}),
                             "total": len(events), "archived": archived, "queued": queued,
                             "last_assessed": storage.get("news-assess-last"), "unassessed": unassessed, "items": items})

    async def assess(request):
        """Queue AI assessments for the worker: {"key": ...} for one article, or {"all": true, "filters": {...}} for
        every unassessed article the news page is showing (at most 200 at a time). The worker does them a few per
        cycle, without trading."""
        if refused := denied(request, write=True):
            return refused
        try:
            body = await request.json()
            key, filters = body.get("key"), {str(k): str(v) for k, v in (body.get("filters") or {}).items()}
        except (ValueError, AttributeError):
            return JSONResponse({"error": "Bad request."}, 400)
        audit = store.audit().drop_duplicates("event_key").set_index("event_key")
        events, _ = matching(filters, audit)
        keys = [e.key for e in events if e.key not in audit.index and (key == e.key if key else body.get("all") is True)][:200]
        for k in keys:
            storage.put("news-assess:" + k, True)
        return JSONResponse({"queued": len(keys)})

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

    def ai_settings():
        saved = storage.get("ai-settings") or {}
        secondary = os.getenv("EVENT_AI_SECONDARY_PROVIDER", "disabled") == "chat"
        return {
            "primary_provider": os.getenv("EVENT_AI_PROVIDER", "disabled"),
            "primary_model": saved.get("primary_model") or os.getenv("EVENT_AI_MODEL", "disabled"),
            "secondary_ready": secondary and bool(os.getenv("EVENT_AI_SECONDARY_KEY")),
            "secondary_model": saved.get("secondary_model") or os.getenv("EVENT_AI_SECONDARY_MODEL", ""),
            "monthly_usd": float(saved.get("monthly_usd", os.getenv("EVENT_AI_MONTHLY_USD", "1"))),
            "share": share if (share := saved.get("share", os.getenv("EVENT_AI_SECONDARY_SHARE") or "auto")) == "auto"
            else float(share),
        }

    async def ai(request):
        """GET: AI settings, model choices and daily usage (UTC days, last 14). PUT: save model/split choices."""
        if refused := denied(request, write=request.method == "PUT"):
            return refused
        if request.method == "PUT":
            try:
                body = await request.json()
                current = ai_settings()
                primary = str(body.get("primary_model", current["primary_model"]))
                secondary = str(body.get("secondary_model", current["secondary_model"]))
                monthly = float(body.get("monthly_usd", current["monthly_usd"]))
                share = body.get("share", current["share"])
                share = share if share == "auto" else float(share)
            except (ValueError, AttributeError, TypeError):
                return JSONResponse({"error": "Bad request."}, 400)
            if current["primary_provider"] == "clairic" and primary not in CLOUDFLARE_MODELS:
                return JSONResponse({"error": "Unknown Cloudflare model."}, 400)
            if secondary != current["secondary_model"] and secondary not in vercel_models():
                return JSONResponse({"error": "That model is not on Vercel AI Gateway with JSON output."}, 400)
            if not 0 <= monthly <= MAX_MONTHLY_USD:
                return JSONResponse({"error": f"The Vercel budget must be $0–${MAX_MONTHLY_USD} a month."}, 400)
            if share != "auto" and not 0 <= share <= 1:
                return JSONResponse({"error": "The split must be between 0% and 100%."}, 400)
            storage.put("ai-settings", {"primary_model": primary, "secondary_model": secondary, "share": share,
                                        "monthly_usd": monthly})
        since = (datetime.now(UTC) - timedelta(days=13)).date().isoformat()
        with storage.tx() as conn:
            rows = conn.execute("SELECT model, payload, response, latency, completed_at FROM event_decisions "
                                "WHERE completed_at >= ? ORDER BY completed_at", (since,)).fetchall()
        days: dict = {}
        for model, payload, response, latency, completed_at in rows:
            day = completed_at[:10]
            try:
                raw = json.loads(response)
                usage = raw.get("usage") or (raw.get("result") or {}).get("usage") or {}
            except (ValueError, AttributeError):
                usage = {}
            provider, _, name = model.partition(":")
            row = days.setdefault(day, {"day": day, "neurons": 0.0, "cf_usd": 0.0, "vercel_usd": 0.0, "models": {}})
            m = row["models"].setdefault(model, {"calls": 0, "failures": 0, "input": 0, "output": 0, "latency": 0.0})
            m["calls"] += 1
            m["failures"] += json.loads(payload).get("reasoning") == PROVIDER_FAILURE
            m["input"] += usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            m["output"] += usage.get("output_tokens") or usage.get("completion_tokens") or 0
            m["latency"] += latency
            rate = neurons_per_million(name, day) if provider == "clairic" else None
            row["neurons"] += rate * (usage.get("input_tokens") or 0) / 1e6 if rate else 0
            row["vercel_usd"] += (usage.get("cost") or 0) if provider == "chat" else 0
        for row in days.values():
            row["cf_usd"] = max(0.0, row["neurons"] - FREE_NEURONS) * NEURON_USD
            for m in row["models"].values():
                m["latency"] /= m["calls"]
        settings = ai_settings()
        monthly = settings["monthly_usd"]
        throttled = storage.get("ai-throttled")
        return JSONResponse({"settings": settings, "free_neurons": FREE_NEURONS, "max_monthly_usd": MAX_MONTHLY_USD,
                             "budget": {"monthly_usd": monthly, **budget(storage, monthly),
                                        "rpm": int(os.getenv("EVENT_AI_SECONDARY_RPM", "10")),
                                        "balance": vercel_credits() if settings["secondary_ready"] else None,
                                        "throttled": throttled if throttled and throttled["until"] >
                                        datetime.now(UTC).isoformat() else None},
                             "options": {"primary": list(CLOUDFLARE_MODELS) if settings["primary_provider"] == "clairic"
                                         else [settings["primary_model"]],
                                         "secondary": vercel_models() if settings["secondary_ready"] else []},
                             "days": sorted(days.values(), key=lambda r: r["day"])})

    routes = [Route("/api/config", config), Route("/api/live", live), Route("/api/orders", orders),
              Route("/api/news", news), Route("/api/news/assess", assess, methods=["POST"]),
              Route("/api/watchlist", save_watchlist, methods=["PUT"]),
              Route("/api/control/{action}", control, methods=["POST"]),
              Route("/api/ai", ai, methods=["GET", "PUT"])]
    if WEB_DIST.is_dir():
        routes.append(Mount("/", StaticFiles(directory=WEB_DIST, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
