"""Dashboard API for worker status, paper trading controls, order journal, and news audit."""

import asyncio
import hmac
import json
import math
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from botfolio.config import AppConfig, load_credentials, watchlist
from botfolio.data.alpaca import AlpacaClient
from botfolio.data.calendar import ny_date
from botfolio.events.activity import news_performance, strategy_performance, trade_activity
from botfolio.events.ai import (
    CLAUDE_MODELS,
    CLOUDFLARE_MODELS,
    FREE_NEURONS,
    PLANS,
    SLOTS,
    Router,
    budget,
    neurons_per_million,
    plan_usage,
)
from botfolio.events.models import Event
from botfolio.events.paper import PaperExecutor, configured
from botfolio.events.review import chart_time, review
from botfolio.events.store import EventStore
from botfolio.storage import Storage

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
MAX_WATCHLIST = 500
NEURON_USD = 0.011 / 1000
MAX_MONTHLY_USD = 50  # slider ceiling; a typo should not spend real money
PROVIDER_FAILURE = "Provider failure or invalid structured output"
_models: dict = {}
_credits: dict = {}


def gateway() -> str:
    """The vercel slot's service: OpenRouter or Vercel AI Gateway, told apart by its URL."""
    return "OpenRouter" if "openrouter.ai" in os.getenv("EVENT_AI_VERCEL_URL", "") else "Vercel"


LABELS = {"claude": "Claude plan (local claude -p)", "chatgpt": "ChatGPT plan (signed in)",
          "cloudflare": "Cloudflare Workers AI"}


def gateway_credits() -> float | None:
    """Remaining gateway credit in USD, cached for five minutes; None when unknown or unlimited."""
    key = os.getenv("EVENT_AI_VERCEL_KEY", "")
    if key and _credits.get("at", 0) < datetime.now(UTC).timestamp() - 300:
        try:
            auth = {"Authorization": "Bearer " + key}
            if gateway() == "OpenRouter":
                left = requests.get("https://openrouter.ai/api/v1/key", timeout=10, headers=auth).json()["data"]["limit_remaining"]
            else:
                left = requests.get("https://ai-gateway.vercel.sh/v1/credits", timeout=10, headers=auth).json()["balance"]
            _credits.update(at=datetime.now(UTC).timestamp(), balance=None if left is None else float(left))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            pass
    return _credits.get("balance")


def provider_models(name: str) -> list[str]:
    """Models a provider offers with JSON output, fetched ones cached for an hour. On OpenRouter, only free ones."""
    if name == "claude":
        return list(CLAUDE_MODELS)
    if name == "cloudflare":
        return list(CLOUDFLARE_MODELS) if os.getenv("EVENT_AI_PROVIDER") == "clairic" else []
    cache = _models.setdefault(name, {})
    if cache.get("at", 0) < datetime.now(UTC).timestamp() - 3600:
        try:
            if name == "chatgpt":
                status = os.getenv("EVENT_AI_CHATGPT_URL", "").rsplit("/", 1)[0] + "/status"
                ids = requests.get(status, timeout=10).json()["models"]
            elif gateway() == "OpenRouter":
                data = requests.get("https://openrouter.ai/api/v1/models", timeout=10).json()["data"]
                ids = [m["id"] for m in data if m["id"].endswith(":free")
                       and "response_format" in (m.get("supported_parameters") or [])]
            else:
                data = requests.get("https://ai-gateway.vercel.sh/v1/models", timeout=10).json()["data"]
                ids = [m["id"] for m in data if m.get("type") == "language"
                       and "response_format" in m.get("supported_parameters", [])]
            cache.update(at=datetime.now(UTC).timestamp(), ids=sorted(ids))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return cache.get("ids", [])
    return cache["ids"]


SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}")


def kill_file() -> Path:
    return Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL"))


def create_app(cfg: AppConfig) -> Starlette:
    storage = Storage(cfg.db_path)
    store = EventStore(storage)
    trade_cache = {}
    trade_loads = {}
    trade_slots = asyncio.Semaphore(4)

    @asynccontextmanager
    async def lifespan(_):
        warmup = asyncio.create_task(preload_trades())
        try:
            yield
        finally:
            warmup.cancel()
            await asyncio.gather(warmup, return_exceptions=True)
            loads = list(trade_loads.values())
            for task in loads:
                task.cancel()
            await asyncio.gather(*loads, return_exceptions=True)
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
                             "benchmarks": storage.get("desk-benchmarks") or {
                                 "purchase_date": None, "annual_rate_pct": 5.75},
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
            "risk_regime": storage.get(f"risk-regime:{ny_date(now)}"),
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
                "momentum": {"enabled": cfg.strategies.momentum.enabled, **limits["momentum"],
                             "symbols": cfg.strategies.momentum.symbols},
                **{name: {"enabled": getattr(cfg.strategies, name).enabled, **limits[name],
                          "symbols": getattr(cfg.strategies, name).symbols}
                   for name in ("orb", "vwap_reversion", "pullback")},
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
            "ai_ready": bool((st := ai_settings())["models"].keys() & set(st["order"])),
            "intents": [{k: row[k] for k in keep if row.get(k) is not None}
                        for row in journal() if row.get("state") != "closed"],
        })

    async def orders(request):
        """Every order the worker has recorded, newest first."""
        return denied(request) or JSONResponse(
            sorted(journal(), key=lambda row: row.get("opened") or "", reverse=True)[:200])

    async def load_trade(key, payload):
        row = {"key": key, "strategy": "news", **json.loads(payload)}
        with storage.tx() as conn:
            article = (conn.execute("SELECT payload FROM events WHERE event_key=?", (key,)).fetchone()
                       if row["strategy"] == "news" else None)

        context = row.get("context", {}).copy()
        if article:
            event = Event.model_validate_json(article[0])
            context.update(article=event.text[:400], source=event.source, url=event.source_url)
            context.setdefault("at", event.content_at.isoformat())
        if context.get("at"):
            context["time"] = chart_time(datetime.fromisoformat(context["at"]))
        credentials = load_credentials(required=False)
        executor = PaperExecutor(AlpacaClient(credentials, timeout=8, max_retries=1), store) if credentials else None
        async with trade_slots:
            detail = await asyncio.to_thread(trade_activity, executor, row, cfg.data.feed, context)
        detail["submitted_time"] = chart_time(datetime.fromisoformat(row["opened"])) if row.get("opened") else None
        detail["order"] = row
        if not (detail.get("error") or detail.get("fill_error")):
            timestamps = [datetime.fromisoformat(fill["at"]) for fill in [detail["entry"], *detail["exits"]]
                          if fill and fill["at"]]
            if row.get("opened"):
                timestamps.append(datetime.fromisoformat(row["opened"]))
            settled = (row["state"] == "closed" and detail["bars"] and timestamps
                       and datetime.now(UTC) - max(timestamps) > timedelta(days=5))
            identity = (key, payload)
            trade_cache.pop(identity, None)
            trade_cache[identity] = (time.monotonic() + (86400 if settled else 60), detail)
            if len(trade_cache) > 256:
                trade_cache.pop(next(iter(trade_cache)))
        return detail

    async def cached_trade(key, payload, *, wait_for_refresh=False):
        identity = (key, payload)
        cached = trade_cache.get(identity)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        if identity not in trade_loads:
            async def refresh():
                try:
                    return await load_trade(key, payload)
                finally:
                    trade_loads.pop(identity, None)

            trade_loads[identity] = asyncio.create_task(refresh())
        if cached and not wait_for_refresh:
            return cached[1]
        return await asyncio.shield(trade_loads[identity])

    async def preload_trades():
        while True:
            if load_credentials(required=False):
                with storage.tx() as conn:
                    recent = conn.execute(
                        "SELECT event_key,payload FROM event_orders "
                        "ORDER BY json_extract(payload, '$.opened') DESC LIMIT 12"
                    ).fetchall()
                for i in range(0, len(recent), 2):
                    await asyncio.gather(
                        *(cached_trade(key, payload, wait_for_refresh=True) for key, payload in recent[i:i + 2]),
                        return_exceptions=True,
                    )
            await asyncio.sleep(15)

    performance_cache = {}

    async def strategies_performance(request):
        """Gross realized and open P&L per strategy, from Alpaca Paper fills. Cached for a minute."""
        if refused := denied(request):
            return refused
        if (cached := performance_cache.get("at")) and time.monotonic() - cached < 60:
            return JSONResponse(performance_cache["body"])
        credentials = load_credentials(required=False)
        if not credentials:
            return JSONResponse({"error": "Connect Alpaca Paper to load strategy P&L."}, 503)
        executor = PaperExecutor(AlpacaClient(credentials, timeout=8, max_retries=1), store)
        try:
            async with trade_slots:
                body = await asyncio.to_thread(strategy_performance, executor, journal())
        except Exception:
            return JSONResponse({"error": "Alpaca Paper fills could not be loaded. Try again shortly."}, 502)
        performance_cache.update(at=time.monotonic(), body=body)
        return JSONResponse(body)

    async def news_performance_route(request):
        """Gross P&L of news trades by article source and by AI model. Cached for a minute."""
        if refused := denied(request):
            return refused
        if (cached := performance_cache.get("news_at")) and time.monotonic() - cached < 60:
            return JSONResponse(performance_cache["news"])
        credentials = load_credentials(required=False)
        if not credentials:
            return JSONResponse({"error": "Connect Alpaca Paper to load news P&L."}, 503)
        executor = PaperExecutor(AlpacaClient(credentials, timeout=8, max_retries=1), store)
        try:
            async with trade_slots:
                body = await asyncio.to_thread(news_performance, executor, journal(), store)
        except Exception:
            return JSONResponse({"error": "Alpaca Paper fills could not be loaded. Try again shortly."}, 502)
        performance_cache.update(news_at=time.monotonic(), news=body)
        return JSONResponse(body)

    async def order_detail(request):
        if refused := denied(request):
            return refused
        key = request.query_params.get("key")
        with storage.tx() as conn:
            record = conn.execute("SELECT payload FROM event_orders WHERE event_key=?", (key,)).fetchone()
        if not record:
            return JSONResponse({"error": "Journal entry not found."}, 404)
        return JSONResponse(await cached_trade(key, record[0]))

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

    reviews: dict = {}

    async def news_review(request):
        """AI news calls for one session scored against the prices that followed. Finished sessions are cached;
        the current one is refetched at most once a minute."""
        if refused := denied(request):
            return refused
        day = request.query_params.get("day")
        hit = reviews.get(day)
        if hit and (hit[1] is None or time.monotonic() - hit[1] < 60):
            return JSONResponse(hit[0])
        try:
            client = AlpacaClient(load_credentials(), timeout=30, max_retries=2)
            data = await asyncio.to_thread(review, store, client, cfg.data.feed, day)
        except Exception as exc:  # missing keys or a market-data outage: the page says so instead of failing blank
            return JSONResponse({"error": f"Could not load prices: {exc}"}, 502)
        finished = data["day"] is not None and data["day"] < str(ny_date(datetime.now(UTC)))
        reviews[day] = reviews[data["day"]] = (data, None if finished else time.monotonic())
        return JSONResponse(data)

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
            return JSONResponse({"error": f"Enter 1–{MAX_WATCHLIST} valid ticker symbols separated by commas or spaces."}, 400)
        storage.put("watchlist", symbols)
        return JSONResponse({"symbols": symbols})

    async def save_benchmarks(request):
        if refused := denied(request, write=True):
            return refused
        try:
            body = await request.json()
            purchase_date, rate = body["purchase_date"], body["annual_rate_pct"]
            if purchase_date is not None:
                parsed = date.fromisoformat(purchase_date)
                if parsed.isoformat() != purchase_date or parsed > ny_date(datetime.now(UTC)):
                    raise ValueError
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                raise ValueError
        except (ValueError, KeyError, TypeError, OverflowError):
            return JSONResponse({"error": "Enter a valid start date through today and a nonnegative annual rate."}, 400)
        settings = {"purchase_date": purchase_date, "annual_rate_pct": rate}
        storage.put("desk-benchmarks", settings)
        return JSONResponse(settings)

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
        router = Router(os.getenv("EVENT_AI_EXPERIMENT", "cloudflare"))  # the worker's own reading of env + settings
        router.configure(saved)
        return {"order": router.order, "monthly_usd": router.monthly_usd,
                "models": {name: ai.model for name, ai in router.ai.items() if ai.provider != "disabled"}}

    async def ai(request):
        """GET: AI provider order, model choices and daily usage (UTC days, last 14). PUT: save order/models/budget."""
        if refused := denied(request, write=request.method == "PUT"):
            return refused
        if request.method == "PUT":
            try:
                body = await request.json()
                current = ai_settings()
                order = [str(name) for name in body.get("order", current["order"])]
                models = {**current["models"], **{str(k): str(v) for k, v in (body.get("models") or {}).items()}}
                monthly = float(body.get("monthly_usd", current["monthly_usd"]))
            except (ValueError, AttributeError, TypeError):
                return JSONResponse({"error": "Bad request."}, 400)
            if len(set(order)) != len(order) or not set(order) <= set(SLOTS):
                return JSONResponse({"error": "Provider order must list known providers once each."}, 400)
            for name, model in models.items():
                if name not in current["models"]:
                    return JSONResponse({"error": f"{name} is not configured in .env."}, 400)
                if model != current["models"][name] and model not in provider_models(name):
                    return JSONResponse({"error": f"{model} is not available on {name} with JSON output."}, 400)
            if not 0 <= monthly <= MAX_MONTHLY_USD:
                return JSONResponse({"error": f"{gateway()} monthly budget must be between $0 and ${MAX_MONTHLY_USD}."}, 400)
            storage.put("ai-settings", {"order": order, "models": models, "monthly_usd": monthly})
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
        now = datetime.now(UTC).isoformat()
        throttled = {k: v for k, v in (storage.get("ai-throttled") or {}).items() if k in SLOTS and v["until"] > now}
        providers = [{"name": name, "label": LABELS.get(name, gateway() if name == "vercel" else name),
                      "ready": name in settings["models"], "model": settings["models"].get(name),
                      "options": provider_models(name) if name in settings["models"] else []} for name in SLOTS]
        return JSONResponse({"settings": settings, "providers": providers, "free_neurons": FREE_NEURONS,
                             "max_monthly_usd": MAX_MONTHLY_USD,
                             "budget": {"monthly_usd": monthly, **budget(storage, monthly),
                                        "rpm": int(os.getenv("EVENT_AI_VERCEL_RPM", "10")),
                                        "plans": {p: plan_usage(storage, p) for p in PLANS if p in settings["models"]},
                                        "balance": gateway_credits() if "vercel" in settings["models"] else None,
                                        "throttled": throttled},
                             "days": sorted(days.values(), key=lambda r: r["day"])})

    routes = [Route("/api/config", config), Route("/api/live", live), Route("/api/orders", orders),
              Route("/api/orders/detail", order_detail),
              Route("/api/strategies/performance", strategies_performance),
              Route("/api/news", news), Route("/api/news/performance", news_performance_route), Route("/api/news/review", news_review), Route("/api/news/assess", assess, methods=["POST"]),
              Route("/api/watchlist", save_watchlist, methods=["PUT"]),
              Route("/api/desk-benchmarks", save_benchmarks, methods=["PUT"]),
              Route("/api/control/{action}", control, methods=["POST"]),
              Route("/api/ai", ai, methods=["GET", "PUT"])]
    if WEB_DIST.is_dir():
        routes.append(Mount("/", StaticFiles(directory=WEB_DIST, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)
