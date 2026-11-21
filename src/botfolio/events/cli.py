"""Event commands share the existing configuration, credentials, bar cache and SQLite file."""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from botfolio.config import load_credentials
from botfolio.data.alpaca import AlpacaClient
from botfolio.data.calendar import frame_to_bars, get_calendar
from botfolio.data.loader import load_history
from botfolio.events.ai import AI, analyze
from botfolio.events.models import Event
from botfolio.events.paper import PaperExecutor, configured, event_is_fresh
from botfolio.events.providers import AlpacaNews, SECFilings
from botfolio.events.research import market_context, replay
from botfolio.events.store import EventStore
from botfolio.storage import Storage

log = logging.getLogger(__name__)


def timestamp(value):
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return ts


def command(cfg, args):
    storage = Storage(cfg.db_path)
    store = EventStore(storage)
    if args.event_cmd == "import":
        events = [Event.model_validate_json(line) for line in Path(args.path).read_text().splitlines() if line.strip()]
        print(json.dumps({"inserted": sum(store.add(e) for e in events), "rows": len(events)}))
    elif args.event_cmd == "report":
        print(store.reports().to_json(orient="records"))
    elif args.event_cmd == "ml":
        import pandas as pd

        from botfolio.events.ml import walk_forward
        Path(args.output).write_text(json.dumps(walk_forward(pd.read_csv(args.path)), indent=2, allow_nan=False))
    elif args.event_cmd == "watch":
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.db_path + ".events.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            asyncio.run(watch(cfg, store, args.experiment, args.once))
    elif args.event_cmd == "replay":
        if args.run_id in set(store.reports().run_id) or args.run_id + "-technical" in set(storage.runs().run_id):
            raise ValueError("run id already exists; use a new id to preserve prior research")
        start, end = timestamp(args.start), timestamp(args.end)
        train_end, validation_end = timestamp(args.train_end), timestamp(args.validation_end)
        if not start < train_end < validation_end < end:
            raise ValueError("require start < train-end < validation-end < end")
        events = [e for e in store.events() if start <= e.content_at < end]
        symbols = sorted({e.ticker for e in events} | {"SPY"})
        client = None if args.cached else AlpacaClient(load_credentials())
        bars_df = load_history(client, storage, get_calendar(), symbols,
            (start-timedelta(days=7)).date(), (end+timedelta(days=4)).date(), cfg.data.feed, cfg.data.adjustment)
        bars = frame_to_bars(bars_df, get_calendar())
        ai = AI()
        for event in events:
            # Unverified historical articles must never be fed to the model for replay.
            try:
                at = event.available_at
            except ValueError:
                continue
            analyze(store, event, args.experiment, ai, market_context(bars, event, at))
        decisions = {e.key: store.decision(e.key, args.experiment) for e in events}
        decisions = {k: v for k, v in decisions.items() if v is not None}
        report = replay(events, decisions, bars_df, train_end=train_end, validation_end=validation_end,
                        latency=args.latency, costs=cfg.costs, seed=cfg.seed)
        from botfolio.backtest import run_backtest
        from botfolio.config import RiskConfig

        comparison_cfg = cfg.model_copy(update={"symbols": symbols, "risk": RiskConfig(
            starting_capital=10000, risk_per_trade_pct=.25, daily_loss_limit_pct=1,
            max_position_pct=25, max_gross_exposure_pct=100, max_open_positions=1)})
        comparison_id, _ = run_backtest(comparison_cfg, storage, client, start.date(), end.date(),
            run_id=args.run_id + "-technical", bars_df=bars_df[(bars_df.ts >= start) & (bars_df.ts < end)],
            monte_carlo=False, notes="Technical/random/buy-and-hold comparison; full-period exposure differs from event replay")
        report["technical_comparison_run"] = comparison_id
        report["experiment"] = args.experiment
        report["source_events"] = len(events)
        store.research(args.run_id, report)
        Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False))
        print(f"Saved {len(report['trades'])} trade/control rows and {len(report['exclusions'])} exclusions to {args.output}")
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
    ai = AI()
    paper = PaperExecutor(AlpacaClient(creds, timeout=5, max_retries=1), store,
                          int(os.getenv("EVENT_HOLD_MINUTES", "15")))
    monitor = None if once else asyncio.create_task(execution_monitor(paper, store))
    try:
        while True:
            now = datetime.now(UTC)
            if once:
                await execution_monitor(paper, store, once=True)
            try:
                watchlist = store.storage.get("event-watchlist") or cfg.symbols
                providers[0].symbols = watchlist
                results = await asyncio.gather(*(p.poll(now-timedelta(days=1), now) for p in providers),
                                               return_exceptions=True)
                failures = 0
                for result in results:
                    if isinstance(result, BaseException):
                        failures += 1
                        log.error("event provider failed", extra={"error": str(result)})
                        continue
                    for event in result:
                        store.add(event)
                events = store.events()
                store.storage.put("event-data-status", dict(at=datetime.now(UTC).isoformat(),
                    provider_failures=failures,
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
                    if configured() and state.get("phase") == "ready" and not failures:
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
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


def parser(sub):
    event = sub.add_parser("events", help="auditable news/filing research and optional paper execution")
    commands = event.add_subparsers(dest="event_cmd", required=True)
    imp = commands.add_parser("import", help="import provenance-checked JSONL snapshots")
    imp.add_argument("path")
    commands.add_parser("report")
    watch_parser = commands.add_parser("watch")
    watch_parser.add_argument("--experiment", required=True)
    watch_parser.add_argument("--once", action="store_true")
    ml = commands.add_parser("ml")
    ml.add_argument("path")
    ml.add_argument("--output", default="event-ml.json")
    research = commands.add_parser("replay")
    for arg in ("start", "end", "train-end", "validation-end", "experiment", "run-id"):
        research.add_argument("--"+arg, required=True)
    research.add_argument("--latency", type=float, default=30)
    research.add_argument("--cached", action="store_true")
    research.add_argument("--output", default="event-research.json")
