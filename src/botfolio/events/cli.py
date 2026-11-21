"""The paper worker: news ingestion, the combined strategies and paper execution."""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
from datetime import UTC, datetime, timedelta

from botfolio.config import load_credentials
from botfolio.config import watchlist as load_watchlist
from botfolio.data.alpaca import AlpacaClient
from botfolio.data.calendar import frame_to_bars, get_calendar
from botfolio.events.ai import AI, analyze
from botfolio.events.context import market_context
from botfolio.events.paper import PaperExecutor, configured, event_is_fresh
from botfolio.events.providers import ISSUER_FEEDS, AlpacaNews, IssuerNews, SECFilings
from botfolio.events.store import EventStore
from botfolio.storage import Storage
from botfolio.strategies import StrategyRunner, strategy_loop

log = logging.getLogger(__name__)


def command(cfg, args):
    storage = Storage(cfg.db_path)
    try:
        store = EventStore(storage)
        if args.event_cmd == "decision-stats":
            print(store.decision_stats().to_json(orient="records", indent=2))
        else:
            with open(cfg.db_path + ".events.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                asyncio.run(watch(cfg, store, args.experiment, args.once))
    finally:
        storage.close()
    return 0


async def execution_monitor(paper, store, once=False):
    """Exits have their own worker and client; news and AI latency cannot hold this loop up."""
    while True:
        now = datetime.now(UTC)
        session = get_calendar("NASDAQ").session_for(now)
        status = dict(at=now.isoformat(), enabled=configured(), armed=bool(store.storage.get("event-paper-armed")),
                      market_open=session is not None, phase="analysis only")
        try:
            if configured():
                active = await asyncio.to_thread(paper.maintain, now)
                status.update(phase="ready" if session else "market closed", open_intents=len(active))
        except Exception:
            log.exception("paper supervision failed; entries blocked")
            status.update(phase="error", message="Broker reconciliation failed. Entries are blocked; inspect application logs.")
        store.storage.put("event-execution-state", status)
        if once:
            return
        await asyncio.sleep(5)


async def watch(cfg, store, experiment, once=False):
    creds = load_credentials()
    client = AlpacaClient(creds, timeout=15, max_retries=2)
    providers = [AlpacaNews(client, cfg.symbols)]
    companies = json.loads(os.getenv("SEC_COMPANIES", "{}"))
    if companies:
        providers.append(SECFilings(companies, os.environ["SEC_USER_AGENT"]))
    issuer_feeds = [IssuerNews(ticker, url) for ticker, url in ISSUER_FEEDS.items()]
    ai = AI()
    paper = PaperExecutor(AlpacaClient(creds, timeout=5, max_retries=1), store,
                          int(os.getenv("EVENT_HOLD_MINUTES", "15")), cfg.limits())
    runner = StrategyRunner(cfg, AlpacaClient(creds, timeout=15, max_retries=2), paper, store.storage)
    tasks = [] if once else [asyncio.create_task(execution_monitor(paper, store)),
                             asyncio.create_task(strategy_loop(runner))]
    try:
        while True:
            now = datetime.now(UTC)
            if once:
                await execution_monitor(paper, store, once=True)
                await strategy_loop(runner, once=True)
            try:
                watchlist = load_watchlist(store.storage, cfg)
                providers[0].symbols = watchlist
                active_providers = providers + [p for p in issuer_feeds if p.ticker in watchlist]
                results = await asyncio.gather(*(p.poll(now-timedelta(days=1), now) for p in active_providers),
                                               return_exceptions=True)
                failures = issuer_failures = 0
                for provider, result in zip(active_providers, results, strict=True):
                    if isinstance(result, BaseException):
                        if isinstance(provider, IssuerNews):
                            issuer_failures += 1
                        else:
                            failures += 1
                        log.error("event provider failed", extra={"provider": type(provider).__name__, "error": str(result)})
                        continue
                    for event in result:
                        store.add(event)
                events = store.events()
                store.storage.put("event-data-status", dict(at=datetime.now(UTC).isoformat(),
                    provider_failures=failures, issuer_feed_failures=issuer_failures,
                    latest_content=max((e.content_at for e in events), default=None).isoformat() if events else None))
                for event in sorted(events, key=lambda e: e.content_at, reverse=True):
                    now = datetime.now(UTC)
                    if event.ticker not in watchlist or not event_is_fresh(event, now):
                        continue
                    decision = store.decision(event.key, experiment)
                    if decision is not None:
                        decision = analyze(store, event, experiment, ai, {})
                    else:
                        bars_df = await asyncio.to_thread(client.get_bars, [event.ticker, "SPY"],
                            event.received_at-timedelta(hours=2), event.received_at, "1Min", cfg.data.feed, "raw")
                        context = market_context(frame_to_bars(bars_df, get_calendar()), event, event.received_at)
                        decision = await asyncio.to_thread(analyze, store, event, experiment, ai, context)
                    state = store.storage.get("event-execution-state") or {}
                    if (cfg.strategies.news.enabled and configured() and state.get("phase") == "ready" and not failures
                        and not event.source.startswith("rss:")
                        and (ai.provider != "clairic" or decision[0].recommended_action in {"BUY", "SELL"})):
                        data = await asyncio.to_thread(client._request, "GET",
                            f"https://data.alpaca.markets/v2/stocks/{event.ticker}/quotes/latest",
                            params={"feed": cfg.data.feed})
                        result = await asyncio.to_thread(paper.enter, event, decision[0], data["quote"])
                        log.info("event paper decision", extra={"event": event.key, "result": result})
                store.storage.put("event-heartbeat", datetime.now(UTC).isoformat())
            except Exception:
                log.exception("event cycle failed; no further entries this cycle")
            if once:
                return
            await asyncio.sleep(15 if get_calendar("NASDAQ").is_open(datetime.now(UTC)) else 60)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def parser(sub):
    event = sub.add_parser("events", help="run the combined strategies on Alpaca Paper")
    commands = event.add_subparsers(dest="event_cmd", required=True)
    commands.add_parser("decision-stats", help="summarize saved AI decisions by experiment, model and source")
    watch_parser = commands.add_parser("watch", help="run the news, strategy and execution monitors")
    watch_parser.add_argument("--experiment", default=os.getenv("EVENT_AI_EXPERIMENT") or "clairic-v1")
    watch_parser.add_argument("--once", action="store_true")
