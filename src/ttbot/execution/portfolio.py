"""Independent virtual portfolio per strategy: sizing, risk limits, fills, stops/targets, accounting.

Execution rules (identical in backtest and forward paper trading):
* A signal produced at the close of bar t is filled at the OPEN of the next 1-minute bar of the same
  symbol and session (market order + spread + slippage). Signals at session end are discarded.
* Exits are evaluated on every subsequent 1-minute bar (finer than the 5-minute signal bars):
  - Gap through the stop at the open -> filled at the open (stop slippage).
  - If a bar touches both stop and target, the STOP is assumed first (conservative).
  - Targets are limit orders: they fill only if price trades strictly through the level.
  - Time stop after ``max_hold_bars`` x timeframe minutes, filled at that bar's close.
  - Intraday positions are flattened at the close of the session's last bar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ttbot.config import ExitConfig, RiskConfig
from ttbot.data.models import Bar
from ttbot.execution.costs import CostModel, OrderType
from ttbot.strategies.base import Signal


@dataclass
class Position:
    trade_id: str
    symbol: str
    side: int
    qty: int
    entry_ts: datetime
    entry_ref: float
    entry_fill: float
    entry_fees: float
    stop: float | None
    target: float | None
    stop_dist: float
    deadline: datetime | None
    hold: bool
    signal: Signal


@dataclass(frozen=True)
class Trade:
    trade_id: str
    portfolio: str
    symbol: str
    side: int
    qty: int
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    gross_pnl: float
    costs: float
    net_pnl: float
    return_pct: float  # net P&L / entry notional, in %
    r_multiple: float
    exit_reason: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EquityPoint:
    portfolio: str
    ts: datetime
    equity: float
    cash: float
    open_positions: int


@dataclass
class _Pending:
    signal: Signal


class Portfolio:
    def __init__(
        self,
        name: str,
        timeframe: int,
        risk: RiskConfig,
        exits: ExitConfig,
        costs: CostModel,
    ) -> None:
        self.name = name
        self.timeframe = timeframe
        self.risk = risk
        self.exits = exits
        self.costs = costs
        self.cash = risk.starting_capital
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, _Pending] = {}
        self.last_price: dict[str, float] = {}
        self.day: date | None = None
        self.day_start_equity = risk.starting_capital
        self.trades_today = 0
        self.closed: list[Trade] = []  # drained by the engine
        self.opened: list[Position] = []  # drained by the engine
        self.rejections: dict[str, int] = {}
        self.n_trades = 0

    # ---------------------------------------------------------- accounting
    def equity(self) -> float:
        return self.cash + sum(p.side * p.qty * self.last_price.get(s, p.entry_ref) for s, p in self.positions.items())

    def gross_exposure(self) -> float:
        return sum(p.qty * self.last_price.get(s, p.entry_ref) for s, p in self.positions.items())

    def _reject(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def new_session(self, day: date) -> None:
        if self.day != day:
            self.day = day
            self.day_start_equity = self.equity()
            self.trades_today = 0

    # -------------------------------------------------------------- signals
    def submit(self, sig: Signal, bar: Bar) -> None:
        """Queue a signal (known at the close of ``bar``) for execution at the next bar's open."""
        if sig.symbol in self.positions or sig.symbol in self.pending:
            self._reject("already_in_position")
            return
        if not sig.hold:
            if sig.side < 0 and not self.risk.allow_short:
                self._reject("short_disabled")
                return
            if bar.minutes_to_close < self.exits.no_entry_last_minutes or bar.is_last:
                self._reject("too_close_to_close")
                return
            if sig.atr is None or sig.atr <= 0:
                self._reject("no_atr")
                return
        self.pending[sig.symbol] = _Pending(sig)

    def _stop_distance(self, sig: Signal) -> float:
        assert sig.atr is not None
        ex = self.exits
        if ex.stop_mode == "structure" and sig.stop is not None:
            dist = abs(sig.ref_price - sig.stop)
            return min(max(dist, ex.min_stop_atr * sig.atr), ex.max_stop_atr * sig.atr)
        return ex.atr_stop_mult * sig.atr

    def _open(self, sig: Signal, bar: Bar) -> None:
        ref = bar.open
        equity = self.equity()
        if sig.hold:
            fill = self.costs.fill_price(ref, 1, "market")
            qty = math.floor(self.cash * 0.999 / fill)
            if qty < 1:
                self._reject("insufficient_cash")
                return
            fees = self.costs.fees(qty, fill, 1)
            self._book_entry(sig, bar, qty, ref, fill, fees, None, None, 0.0, None)
            return
        if self.trades_today >= self.risk.max_trades_per_day:
            self._reject("max_trades_per_day")
            return
        if len(self.positions) >= self.risk.max_open_positions:
            self._reject("max_open_positions")
            return
        if equity <= self.day_start_equity * (1 - self.risk.daily_loss_limit_pct / 100.0):
            self._reject("daily_loss_limit")
            return
        stop_dist = self._stop_distance(sig)
        if sig.inverse:
            stop_dist, target_dist = self.exits.reward_risk * stop_dist, stop_dist
        else:
            target_dist = self.exits.reward_risk * stop_dist
        fill = self.costs.fill_price(ref, sig.side, "market")
        qty_risk = equity * self.risk.risk_per_trade_pct / 100.0 / stop_dist
        qty_pos = equity * self.risk.max_position_pct / 100.0 / fill
        room = equity * self.risk.max_gross_exposure_pct / 100.0 - self.gross_exposure()
        qty = math.floor(min(qty_risk, qty_pos, max(room, 0.0) / fill))
        if qty < 1:
            self._reject("size_below_one_share")
            return
        fees = self.costs.fees(qty, fill, sig.side)
        stop = fill - sig.side * stop_dist
        target = fill + sig.side * target_dist
        deadline = bar.ts + timedelta(minutes=self.exits.max_hold_bars * self.timeframe)
        self._book_entry(sig, bar, qty, ref, fill, fees, stop, target, stop_dist, deadline)

    def _book_entry(
        self,
        sig: Signal,
        bar: Bar,
        qty: int,
        ref: float,
        fill: float,
        fees: float,
        stop: float | None,
        target: float | None,
        stop_dist: float,
        deadline: datetime | None,
    ) -> None:
        trade_id = f"{self.name}|{sig.symbol}|{bar.ts.isoformat()}"
        self.cash -= sig.side * qty * fill + fees
        self.positions[sig.symbol] = Position(
            trade_id, sig.symbol, sig.side, qty, bar.ts, ref, fill, fees, stop, target, stop_dist, deadline, sig.hold, sig
        )
        self.opened.append(self.positions[sig.symbol])
        self.trades_today += 1
        self.n_trades += 1

    def _close(self, pos: Position, ts: datetime, ref: float, order_type: OrderType, reason: str) -> None:
        exit_side = -pos.side
        fill = self.costs.fill_price(ref, exit_side, order_type)
        fees = self.costs.fees(pos.qty, fill, exit_side)
        self.cash += pos.side * pos.qty * fill - fees
        gross = pos.side * pos.qty * (ref - pos.entry_ref)
        net = pos.side * pos.qty * (fill - pos.entry_fill) - fees - pos.entry_fees
        notional = pos.qty * pos.entry_ref
        risk_dollars = pos.qty * pos.stop_dist if pos.stop_dist > 0 else float("nan")
        meta: dict[str, Any] = {k: v for k, v in pos.signal.meta.items() if isinstance(v, (int, float, str))}
        meta.update(stop=pos.stop, target=pos.target, signal_ts=pos.signal.ts.isoformat())
        if pos.signal.atr:
            meta["stop_atr"] = pos.stop_dist / pos.signal.atr
        self.closed.append(
            Trade(
                trade_id=pos.trade_id,
                portfolio=self.name,
                symbol=pos.symbol,
                side=pos.side,
                qty=pos.qty,
                entry_ts=pos.entry_ts,
                entry_price=pos.entry_fill,
                exit_ts=ts,
                exit_price=fill,
                gross_pnl=gross,
                costs=gross - net,
                net_pnl=net,
                return_pct=100.0 * net / notional if notional else 0.0,
                r_multiple=net / risk_dollars if not math.isnan(risk_dollars) else float("nan"),
                exit_reason=reason,
                meta=meta,
            )
        )
        del self.positions[pos.symbol]

    # ------------------------------------------------------------ bar logic
    def on_bar(self, bar: Bar) -> None:
        """Process a 1-minute bar: fill pending entries at its open, then evaluate exits within it."""
        sym = bar.symbol
        if bar.session:
            self.new_session(bar.session)
        pend = self.pending.pop(sym, None)
        if pend is not None:
            sig = pend.signal
            if sig.ts <= bar.ts and (sig.hold or bar.session == sig.session):
                self.last_price[sym] = bar.open
                self._open(sig, bar)
            else:
                self._reject("stale_signal")
        self.last_price[sym] = bar.close
        pos = self.positions.get(sym)
        if pos is None or pos.hold:
            return
        assert pos.stop is not None and pos.target is not None
        s = pos.side
        # 1) gap through stop / target at the open
        if (s > 0 and bar.open <= pos.stop) or (s < 0 and bar.open >= pos.stop):
            self._close(pos, bar.ts, bar.open, "stop", "stop_gap")
            return
        if (s > 0 and bar.open > pos.target) or (s < 0 and bar.open < pos.target):
            self._close(pos, bar.ts, bar.open, "limit", "target_gap")
            return
        # 2) intrabar: stop first if both touched (conservative)
        hit_stop = bar.low <= pos.stop if s > 0 else bar.high >= pos.stop
        hit_tgt = bar.high > pos.target if s > 0 else bar.low < pos.target
        if hit_stop:
            self._close(pos, bar.end, pos.stop, "stop", "stop")
            return
        if hit_tgt:
            self._close(pos, bar.end, pos.target, "limit", "target")
            return
        # 3) time stop
        if pos.deadline is not None and bar.end >= pos.deadline:
            self._close(pos, bar.end, bar.close, "market", "time")

    def flatten(self, symbol: str, ts: datetime, price: float, reason: str) -> None:
        """Close an intraday position at ``price`` (market) and drop pending orders for ``symbol``."""
        self.pending.pop(symbol, None)
        pos = self.positions.get(symbol)
        if pos is not None and not pos.hold and self.exits.flatten_eod:
            self.last_price[symbol] = price
            self._close(pos, ts, price, "market", reason)

    def snapshot(self, ts: datetime) -> EquityPoint:
        return EquityPoint(self.name, ts, self.equity(), self.cash, len(self.positions))

