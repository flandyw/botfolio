"""Event-driven simulation engine shared by backtest and forward paper-trading modes.

Processing order for every incoming 1-minute bar B (all strictly causal):
  1. If B proves an N-minute window complete (a minute was missing), emit that window to N-minute
     strategies -> their signals queue for B's open.
  2. Every portfolio fills queued orders at B.open and evaluates stops/targets/time exits within B.
  3. B is added to the aggregators; a completed N-minute bar is passed to N-minute strategies.
  4. 1-minute strategies see B. Signals from steps 3-4 queue for the NEXT bar's open.
  5. If B is the session's last bar, intraday positions are flattened at B.close.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from ttbot.config import AppConfig
from ttbot.data.aggregate import BarAggregator
from ttbot.data.models import Bar
from ttbot.execution.costs import CostModel
from ttbot.execution.portfolio import EquityPoint, Portfolio, Position, Trade
from ttbot.strategies.base import Signal, Strategy

log = logging.getLogger(__name__)

ENGINE_STATE_VERSION = 2


class EngineListener(Protocol):
    def on_open(self, portfolio: str, pos: Position) -> None: ...
    def on_close(self, trade: Trade) -> None: ...


@dataclass
class EngineOutput:
    trades: list[Trade] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)

    def extend(self, other: EngineOutput) -> None:
        self.trades += other.trades
        self.signals += other.signals
        self.equity += other.equity


class Engine:
    def __init__(self, cfg: AppConfig, strategies: list[Strategy[Any]]) -> None:
        self.version = ENGINE_STATE_VERSION
        self.cfg = cfg
        self.strategies = strategies
        costs = CostModel(cfg.costs)
        self.portfolios: dict[str, Portfolio] = {
            s.name: Portfolio(s.name, s.timeframe, s.risk or cfg.risk, s.exits or cfg.exits, costs)
            for s in strategies
        }
        self.by_tf: dict[int, list[Strategy[Any]]] = defaultdict(list)
        for s in strategies:
            self.by_tf[s.timeframe].append(s)
        self.aggs = {tf: BarAggregator(tf) for tf in self.by_tf if tf > 1}
        self.session: date | None = None
        self.last_bar: dict[str, Bar] = {}
        self.bars_processed = 0
        self._out = EngineOutput()
        self.listeners: list[EngineListener] = []

    # ------------------------------------------------------------------ io
    def drain(self) -> EngineOutput:
        for p in self.portfolios.values():
            if p.closed:
                self._out.trades += p.closed
                for t in p.closed:
                    for lst in self.listeners:
                        lst.on_close(t)
                p.closed = []
            if p.opened:
                for pos in p.opened:
                    for lst in self.listeners:
                        lst.on_open(p.name, pos)
                p.opened = []
        out, self._out = self._out, EngineOutput()
        return out

    def watermark(self, symbol: str) -> datetime | None:
        b = self.last_bar.get(symbol)
        return b.ts if b else None

    # ---------------------------------------------------------- processing
    def _dispatch(self, bar: Bar) -> None:
        for strat in self.by_tf.get(bar.timeframe, []):
            sig = strat.on_bar(bar)
            if sig is not None:
                self._record_signal(sig)
                self.portfolios[strat.name].submit(sig, bar)

    def _record_signal(self, sig: Signal) -> None:
        self._out.signals.append(
            {
                "portfolio": sig.strategy,
                "symbol": sig.symbol,
                "ts": sig.ts,
                "side": sig.side,
                "price": sig.ref_price,
                "stop": sig.stop,
                "atr": sig.atr,
                "meta": {k: v for k, v in sig.meta.items() if isinstance(v, (int, float, str))},
            }
        )

    def process(self, bar: Bar) -> bool:
        """Feed one session-annotated 1-minute bar. Returns False if it was a duplicate/out of order."""
        if bar.timeframe != 1 or bar.session is None:
            raise ValueError("engine expects session-annotated 1-minute bars")
        prev = self.last_bar.get(bar.symbol)
        if prev is not None and bar.ts <= prev.ts:
            return False
        if self.session is not None and bar.session != self.session:
            self.end_session()
        self.session = bar.session

        for agg in self.aggs.values():
            done = agg.flush_before(bar)
            if done is not None:
                self._dispatch(done)
        for p in self.portfolios.values():
            p.on_bar(bar)
        for agg in self.aggs.values():
            done = agg.add(bar)
            if done is not None:
                self._dispatch(done)
        self._dispatch(bar)
        if bar.is_last:
            for p in self.portfolios.values():
                p.flatten(bar.symbol, bar.end, bar.close, "eod")
        self.last_bar[bar.symbol] = bar
        self.bars_processed += 1
        return True

    def end_session(self) -> None:
        """Close out the current session: flatten anything left open (e.g. when the session's final bar was
        missing from the feed) at the last known price, and snapshot every portfolio's equity."""
        if self.session is None:
            return
        todays = [b for b in self.last_bar.values() if b.session == self.session]
        for b in todays:
            for p in self.portfolios.values():
                p.flatten(b.symbol, b.end, b.close, "eod" if b.is_last else "eod_missing_bar")
        if todays:
            ts = max(b.end for b in todays)
            for p in self.portfolios.values():
                self._out.equity.append(p.snapshot(ts))
        self.session = None

    def snapshot_equity(self, ts: datetime) -> list[EquityPoint]:
        return [p.snapshot(ts) for p in self.portfolios.values()]

    # ------------------------------------------------------------- pickling
    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["listeners"] = []
        return state
