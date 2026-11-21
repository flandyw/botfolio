"""Independent virtual portfolio per strategy: sizing, risk limits, fills, stops/targets, accounting.

Execution rules (identical in backtest and forward paper trading):
* A signal produced at the close of bar t is filled at the OPEN of the next 1-minute bar of the same
  symbol and session (market order + spread + slippage). Signals at session end are discarded.
* ``managed`` signals keep only the hard stop and the session-close flatten; the strategy closes the
  position itself with a ``side=0`` signal, filled at the next bar's open like an entry.
* Exits are evaluated on every subsequent 1-minute bar (finer than the 5-minute signal bars):
  - Gap through the stop at the open -> filled at the open (stop slippage).
  - If a bar touches both stop and target, the STOP is assumed first (conservative).
  - Targets are limit orders: they fill only if price trades strictly through the level.
  - Time stop after ``max_hold_bars`` x timeframe minutes, filled at that bar's close.
  - Intraday positions are flattened at the close of the session's last bar.
  - Strategy exit signals (``Signal.exit``) close at the next bar's open; ``Signal.reverse`` entries close
    an opposite-side position at that open and then enter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from botfolio.config import ExitConfig, RiskConfig, TaxConfig
from botfolio.data.models import Bar
from botfolio.execution.costs import CostModel, OrderType
from botfolio.strategies.base import Signal


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
    dividends: float = 0.0  # net cash dividends received (longs) or paid (shorts) while open


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
class TaxYear:
    short_gains: float = 0.0  # realised gains without the CGT discount
    long_gains: float = 0.0  # realised gains on longs held long enough for the discount
    losses: float = 0.0
    dividends: float = 0.0  # gross, before withholding
    withheld: float = 0.0  # foreign tax already withheld, credited against local tax (offset)


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
        tax: TaxConfig | None = None,
    ) -> None:
        self.name = name
        self.tax = tax or TaxConfig()
        self.tax_year = TaxYear()
        self.fy: int | None = None
        self.loss_carry = 0.0  # net capital losses carried forward
        self.tax_paid = 0.0
        self.timeframe = timeframe
        self.risk = risk
        self.exits = exits
        self.costs = costs
        self.cash = risk.starting_capital
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, _Pending] = {}
        self.exit_pending: set[str] = set()
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
        """Mark-to-market value after this financial year's tax so far (unrealised gains are not taxed)."""
        marked = sum(p.side * p.qty * self.last_price.get(s, p.entry_ref) for s, p in self.positions.items())
        return self.cash + marked - self.tax_due()

    def tax_due(self) -> float:
        """Tax on this financial year's realised gains and dividends. Losses (this year's, then carried) are
        set against undiscounted gains first, then the discount applies to what remains (ATO method)."""
        rate = self.tax.marginal_rate_pct / 100.0
        if rate == 0:
            return 0.0
        t = self.tax_year
        short = t.short_gains - t.losses - self.loss_carry
        long = max(t.long_gains + min(short, 0.0), 0.0)
        gain = max(short, 0.0) + long * (1 - self.tax.cgt_discount_pct / 100.0)
        return gain * rate + max(t.dividends * rate - t.withheld, 0.0)

    def _settle_tax(self) -> None:
        due = self.tax_due()
        self.cash -= due
        self.tax_paid += due
        t = self.tax_year
        self.loss_carry = max(t.losses + self.loss_carry - t.short_gains - t.long_gains, 0.0)
        self.tax_year = TaxYear()

    def gross_exposure(self) -> float:
        return sum(p.qty * self.last_price.get(s, p.entry_ref) for s, p in self.positions.items())

    def _reject(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def credit_dividend(self, symbol: str, per_share: float, withholding_pct: float) -> None:
        """Ex-date of a cash dividend: a long held through the prior close receives it net of withholding
        tax, a short pays it in full to the lender."""
        pos = self.positions.get(symbol)
        if pos is None:
            return
        gross = pos.qty * per_share
        if pos.side > 0:
            self.tax_year.dividends += gross
            self.tax_year.withheld += gross * withholding_pct / 100.0
        amount = gross * ((1 - withholding_pct / 100.0) if pos.side > 0 else -1.0)
        self.cash += amount
        pos.dividends += amount

    def new_session(self, day: date) -> None:
        if self.day != day:
            fy = day.year + (day.month >= self.tax.fy_start_month)
            if self.fy is not None and fy != self.fy:
                self._settle_tax()
            self.fy = fy
            self.day = day
            self.day_start_equity = self.equity()
            self.trades_today = 0

    # -------------------------------------------------------------- signals
    def submit(self, sig: Signal, bar: Bar) -> None:
        """Queue a signal (known at the close of ``bar``) for execution at the next bar's open."""
        if sig.side == 0:  # managed / hold strategies: close whatever is open at the next bar
            if sig.symbol in self.positions:
                self.exit_pending.add(sig.symbol)
            return
        pos = self.positions.get(sig.symbol)
        if sig.exit:
            if pos is None or pos.hold or sig.symbol in self.pending:
                self._reject("exit_without_position")
            else:
                self.pending[sig.symbol] = _Pending(sig)
            return
        reversing = sig.reverse and pos is not None and not pos.hold and pos.side != sig.side
        if reversing and sig.side < 0 and not self.risk.allow_short:
            sig = replace(sig, exit=True)
            if sig.symbol in self.pending:
                self._reject("already_in_position")
            else:
                self.pending[sig.symbol] = _Pending(sig)
            return
        if (pos is not None and not reversing) or sig.symbol in self.pending:
            if not sig.hold:  # hold strategies restate their target every session
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
        if sig.managed and sig.stop is not None and sig.stop != sig.ref_price:
            return abs(sig.ref_price - sig.stop)
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
        no_stop = self.exits.stop_mode == "none"
        stop_dist = 0.0 if no_stop else self._stop_distance(sig)
        if sig.inverse and not sig.managed:
            stop_dist, target_dist = self.exits.reward_risk * stop_dist, stop_dist
        else:
            target_dist = self.exits.reward_risk * stop_dist
        fill = self.costs.fill_price(ref, sig.side, "market")
        qty_pos = equity * self.risk.max_position_pct / 100.0 / fill
        if self.risk.sizing == "risk" and stop_dist > 0:
            qty_risk = equity * self.risk.risk_per_trade_pct / 100.0 / stop_dist
        else:
            qty_risk = qty_pos
        room = equity * self.risk.max_gross_exposure_pct / 100.0 - self.gross_exposure()
        qty = math.floor(min(qty_risk, qty_pos, max(room, 0.0) / fill))
        if qty < 1:
            self._reject("size_below_one_share")
            return
        fees = self.costs.fees(qty, fill, sig.side)
        stop = None if no_stop else fill - sig.side * stop_dist
        target: float | None = None if no_stop else fill + sig.side * target_dist
        deadline: datetime | None = bar.ts + timedelta(minutes=self.exits.max_hold_bars * self.timeframe)
        if sig.managed:
            target = deadline = None
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
        net = pos.side * pos.qty * (fill - pos.entry_fill) - fees - pos.entry_fees + pos.dividends
        capital = net - (pos.dividends if pos.side > 0 else 0.0)  # long dividends are taxed as income instead
        if capital < 0:
            self.tax_year.losses -= capital
        elif pos.side > 0 and (ts.date() - pos.entry_ts.date()).days > self.tax.discount_min_days:
            self.tax_year.long_gains += capital
        else:
            self.tax_year.short_gains += capital
        notional = pos.qty * pos.entry_ref
        risk_dollars = pos.qty * pos.stop_dist if pos.stop_dist > 0 else float("nan")
        meta: dict[str, Any] = {k: v for k, v in pos.signal.meta.items() if isinstance(v, (int, float, str))}
        meta.update(stop=pos.stop, target=pos.target, signal_ts=pos.signal.ts.isoformat(), dividends=pos.dividends)
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
                gross_pnl=gross + pos.dividends,
                costs=gross + pos.dividends - net,
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
                cur = self.positions.get(sym)
                if sig.exit:
                    if cur is not None:
                        self._close(cur, bar.ts, bar.open, "market", "signal")
                else:
                    if cur is not None and sig.reverse and cur.side != sig.side:
                        self._close(cur, bar.ts, bar.open, "market", "reverse")
                    self._open(sig, bar)
            else:
                self._reject("stale_signal")
        self.last_price[sym] = bar.close
        pos = self.positions.get(sym)
        if sym in self.exit_pending:
            self.exit_pending.discard(sym)
            if pos is not None:
                self._close(pos, bar.ts, bar.open, "market", "signal")
                return
        if pos is None or pos.hold:
            return
        if pos.stop is None:  # stop_mode "none": only the strategy, time stop or session close exit
            if pos.deadline is not None and bar.end >= pos.deadline:
                self._close(pos, bar.end, bar.close, "market", "time")
            return
        s = pos.side
        target = pos.target if pos.target is not None else s * math.inf
        # 1) gap through stop / target at the open
        if (s > 0 and bar.open <= pos.stop) or (s < 0 and bar.open >= pos.stop):
            self._close(pos, bar.ts, bar.open, "stop", "stop_gap")
            return
        if (s > 0 and bar.open > target) or (s < 0 and bar.open < target):
            self._close(pos, bar.ts, bar.open, "limit", "target_gap")
            return
        # 2) intrabar: stop first if both touched (conservative)
        hit_stop = bar.low <= pos.stop if s > 0 else bar.high >= pos.stop
        hit_tgt = bar.high > target if s > 0 else bar.low < target
        if hit_stop:
            self._close(pos, bar.end, pos.stop, "stop", "stop")
            return
        if hit_tgt:
            self._close(pos, bar.end, target, "limit", "target")
            return
        # 3) time stop
        if pos.deadline is not None and bar.end >= pos.deadline:
            self._close(pos, bar.end, bar.close, "market", "time")

    def flatten(self, symbol: str, ts: datetime, price: float, reason: str) -> None:
        """Close an intraday position at ``price`` (market) and drop pending orders for ``symbol``."""
        self.pending.pop(symbol, None)
        self.exit_pending.discard(symbol)
        pos = self.positions.get(symbol)
        if pos is not None and not pos.hold and self.exits.flatten_eod:
            self.last_price[symbol] = price
            self._close(pos, ts, price, "market", reason)

    def snapshot(self, ts: datetime) -> EquityPoint:
        return EquityPoint(self.name, ts, self.equity(), self.cash, len(self.positions))

