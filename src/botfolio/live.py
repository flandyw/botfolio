"""Forward paper trading: polls Alpaca minute bars, runs the engine, persists everything, recovers from crashes.

All strategies trade LOCAL virtual portfolios (one per strategy). Optionally one portfolio's orders are
mirrored to the Alpaca PAPER account (BOTFOLIO_MIRROR_STRATEGY) - never to a live account (see safety.py).
"""

from __future__ import annotations

import hashlib
import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from botfolio.analysis import compute_stats
from botfolio.config import AppConfig
from botfolio.data.alpaca import AlpacaClient
from botfolio.data.calendar import frame_to_bars, get_calendar, ny_date
from botfolio.data.loader import cache_key
from botfolio.engine import ENGINE_STATE_VERSION, Engine
from botfolio.execution.portfolio import Portfolio, Position, Trade
from botfolio.storage import Storage
from botfolio.strategies import build_roster

log = logging.getLogger(__name__)

BarFetcher = Callable[[list[str], datetime, datetime], pd.DataFrame]
DividendFetcher = Callable[[list[str], date, date], dict[tuple[str, date], float]]


class PaperMirror:
    """Mirrors one virtual portfolio's entries/exits as market orders on the Alpaca PAPER account."""

    max_age = timedelta(minutes=3)  # never mirror stale events replayed during crash-recovery catch-up

    def __init__(self, client: AlpacaClient, portfolio: str) -> None:
        self.client = client
        self.portfolio = portfolio
        acct = client.get_paper_account()  # raises LiveTradingForbidden if not a paper account
        log.info("paper mirror enabled", extra={"portfolio": portfolio, "account": acct.get("account_number")})

    def _coid(self, trade_id: str, leg: str) -> str:
        return f"botfolio-{leg}-" + hashlib.sha1(trade_id.encode()).hexdigest()[:30]

    def on_open(self, portfolio: str, pos: Position) -> None:
        if portfolio != self.portfolio or datetime.now(UTC) - pos.entry_ts > self.max_age:
            return
        try:
            self.client.submit_paper_order(pos.symbol, pos.qty, "buy" if pos.side > 0 else "sell",
                                           self._coid(pos.trade_id, "in"))
        except Exception:
            log.exception("paper mirror entry failed", extra={"trade_id": pos.trade_id})

    def on_close(self, trade: Trade) -> None:
        if trade.portfolio != self.portfolio or datetime.now(UTC) - trade.exit_ts > self.max_age:
            return
        try:
            self.client.submit_paper_order(trade.symbol, trade.qty, "sell" if trade.side > 0 else "buy",
                                           self._coid(trade.trade_id, "out"))
        except Exception:
            log.exception("paper mirror exit failed", extra={"trade_id": trade.trade_id})


class LiveRunner:
    def __init__(
        self,
        cfg: AppConfig,
        storage: Storage,
        fetch_bars: BarFetcher,
        run_id: str = "forward",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        warmup_sessions: int | None = None,  # default: the longest lookback in the roster
        max_catchup_days: int = 7,
        fetch_dividends: DividendFetcher | None = None,
    ) -> None:
        self.cfg = cfg
        self.storage = storage
        self.fetch_bars = fetch_bars
        self.run_id = run_id
        self.clock = clock
        self.cal = get_calendar(cfg.session.calendar)
        self.warmup_sessions = warmup_sessions or max(s.warmup_sessions for s in build_roster(cfg))
        self.fetch_dividends = fetch_dividends
        self._dividends_day: date | None = None
        self.max_catchup_days = max_catchup_days
        self.engine: Engine | None = None
        self._stop = False
        self.mirror: PaperMirror | None = None

    @property
    def _snap_key(self) -> str:
        return f"engine:{self.run_id}"

    # ------------------------------------------------------------- startup
    def start(self) -> Engine:
        """Load the last engine snapshot (crash recovery) or build and warm up a fresh engine."""
        self.storage.create_run(self.run_id, "forward", self.cfg.model_dump(), self.clock().date().isoformat(), None)
        snap = None
        try:
            snap = self.storage.get(self._snap_key)
        except Exception:
            log.exception("could not unpickle engine snapshot; rebuilding")
        roster_names = [s.name for s in build_roster(self.cfg)]
        if isinstance(snap, Engine) and snap.version == ENGINE_STATE_VERSION and list(snap.portfolios) == roster_names:
            self.engine = snap
            log.info("recovered engine snapshot", extra={"run_id": self.run_id, "bars": snap.bars_processed})
        else:
            if snap is not None:
                log.warning("engine snapshot incompatible with current config/code; starting fresh portfolios")
            self.engine = self._fresh_engine()
        if self.mirror:
            self.engine.listeners = [self.mirror]
        self.storage.update_run(self.run_id, status="running")
        return self.engine

    def _fresh_engine(self) -> Engine:
        """Warm indicators/structure on recent history, then reset portfolios (warm-up trades discarded)."""
        engine = Engine(self.cfg, build_roster(self.cfg))
        now = self.clock()
        today = ny_date(now)
        n_warm = max([self.warmup_sessions, *(s.warmup_sessions for s in engine.strategies)])
        sessions = self.cal.sessions_between(today - timedelta(days=2 * n_warm + 10), today - timedelta(days=1))
        sessions = sessions[-n_warm:]
        if sessions:
            df = self.fetch_bars(self.cfg.symbols, sessions[0].open, sessions[-1].close)
            for bar in frame_to_bars(df, self.cal):
                engine.process(bar)
            engine.end_session()
            engine.drain()
        costs = next(iter(engine.portfolios.values())).costs if engine.portfolios else None
        for name, p in list(engine.portfolios.items()):
            engine.portfolios[name] = Portfolio(name, p.timeframe, p.risk, p.exits, costs or p.costs, self.cfg.tax)
        # buy-and-hold strategies must buy again on the first forward bar
        for s in engine.strategies:
            if s.kind == "buy_hold":
                s._states.clear()
        log.info("fresh engine warmed up", extra={"sessions": len(sessions), "bars": engine.bars_processed})
        return engine

    def _refresh_dividends(self, now: datetime) -> None:
        """Once a day, load ex-dates around today (covers crash catch-up) so they are known before the open."""
        today = ny_date(now)
        if self.fetch_dividends is None or self._dividends_day == today or self.cfg.data.adjustment == "all":
            return
        assert self.engine is not None
        try:
            self.engine.dividends.update(self.fetch_dividends(self.cfg.symbols, today - timedelta(days=self.max_catchup_days + 3), today + timedelta(days=7)))
            self._dividends_day = today
        except Exception:
            log.exception("could not load dividends; retrying next cycle")

    # ---------------------------------------------------------------- loop
    def step(self) -> int:
        """One polling cycle. Returns number of new bars processed."""
        assert self.engine is not None
        eng = self.engine
        now = self.clock()
        self._refresh_dividends(now)
        cutoff = now - timedelta(seconds=self.cfg.bar_delay_seconds)
        marks = [eng.watermark(s) for s in self.cfg.symbols]
        known = [m for m in marks if m is not None]
        if known:
            since = min(known) + timedelta(minutes=1)
            since = max(since, now - timedelta(days=self.max_catchup_days))
        else:
            sess = self.cal.session(ny_date(now))
            since = sess.open if sess else now - timedelta(days=1)
        processed = 0
        if since < cutoff:
            df = self.fetch_bars(self.cfg.symbols, since, cutoff)
            if not df.empty:
                df = df[df["ts"] + pd.Timedelta(minutes=1) <= cutoff]  # completed minutes only
            bars = frame_to_bars(df, self.cal)
            for bar in bars:
                processed += int(eng.process(bar))
            if self.cfg.data.cache_bars and not df.empty:
                self.storage.save_bars(df, cache_key(self.cfg.data.feed, self.cfg.data.adjustment))
        # close the session once the market is shut
        if eng.session is not None:
            sess = self.cal.session(eng.session)
            if sess is None or now >= sess.close + timedelta(minutes=2):
                eng.end_session()
        self._persist(now)
        return processed

    def _persist(self, now: datetime) -> None:
        assert self.engine is not None
        out = self.engine.drain()
        if out.trades:
            self.storage.save_trades(self.run_id, out.trades)
            for t in out.trades:
                log.info("trade closed", extra={"portfolio": t.portfolio, "symbol": t.symbol, "side": t.side,
                                                "net_pnl": round(t.net_pnl, 2), "reason": t.exit_reason})
        if out.signals:
            self.storage.save_signals(self.run_id, out.signals)
        if out.equity:
            self.storage.save_equity(self.run_id, out.equity)
        live_eq = {p.portfolio: p.equity for p in self.engine.snapshot_equity(now)}
        self.storage.put(f"live_equity:{self.run_id}", {"ts": now.isoformat(), "equity": live_eq})
        self.storage.put(self._snap_key, self.engine)
        self.storage.put("heartbeat", now.isoformat())

    def refresh_stats(self) -> dict[str, dict[str, Any]]:
        assert self.engine is not None
        stats = compute_stats(self.cfg, self.storage.trades(self.run_id), self.storage.equity(self.run_id),
                              list(self.engine.portfolios),
                              exits={n: p.exits for n, p in self.engine.portfolios.items()})
        self.storage.save_stats(self.run_id, stats)
        return stats

    def stop(self, *_: Any) -> None:
        log.info("stop requested")
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        if self.engine is None:
            self.start()
        backoff = 5.0
        last_stats_day = None
        while not self._stop:
            try:
                n = self.step()
                if n:
                    log.info("processed bars", extra={"n": n})
                today = ny_date(self.clock())
                if last_stats_day != today and self.engine and self.engine.session is None:
                    self.refresh_stats()
                    last_stats_day = today
                backoff = 5.0
            except Exception:
                log.exception("live step failed; retrying", extra={"backoff_s": backoff})
                self._sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue
            self._sleep(self._seconds_to_next_poll())
        log.info("live runner stopped")

    def _seconds_to_next_poll(self) -> float:
        now = self.clock()
        nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=self.cfg.bar_delay_seconds)
        wait = (nxt - now).total_seconds()
        sess = self.cal.session_for(now) or self.cal.session_for(now - timedelta(minutes=3))
        if sess is None:
            wait = max(wait, min(self.cfg.poll_seconds * 5, 300))  # market closed: poll slowly
        return max(1.0, wait)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))
