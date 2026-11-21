"""Paper-only execution with durable intents, bounded entries and broker reconciliation."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from botfolio.data.alpaca import AlpacaAPIError
from botfolio.data.calendar import get_calendar, ny_date
from botfolio.safety import PAPER_TRADING_URL, assert_paper_url, check_environment

TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
REJECTED = {400, 401, 403, 404, 422}  # definitive broker refusals; anything else is ambiguous
# Per-strategy caps: concurrent positions, notional and risk per position as a percent of equity.
DEFAULT_LIMITS = {"news": dict(max_positions=5, position_pct=25.0, risk_pct=1.0)}


@dataclass(frozen=True)
class Signal:
    """One strategy's request to open a position. Each key reaches the broker at most once."""
    strategy: str
    key: str
    symbol: str
    direction: int                  # 1 long, -1 short
    stop: float | None = None       # absolute protective stop held at the broker
    stop_pct: float | None = None   # or a stop this far from the entry limit
    target_r: float | None = None   # take profit in multiples of the stop distance
    hold_minutes: int | None = None  # timed exit; intraday positions are always flat before the close
    intraday: bool = True           # False: held overnight until the strategy requests an exit
    fractional: bool = False        # long-only simple orders may use fractional shares
    position_pct: float | None = None  # a smaller position than the strategy's cap, as a percent of equity
    fresh: Callable[[datetime], bool] | None = None


def event_is_fresh(event, now):
    # Downloading an old article must never renew its trading eligibility.
    if event.mode != "forward" or now < event.received_at or event.received_at-event.content_at > timedelta(minutes=5):
        return False
    cal = get_calendar("NASDAQ")
    if cal.session_for(event.received_at):
        return now <= event.received_at + timedelta(minutes=5)
    sessions = cal.sessions_between(ny_date(event.received_at), ny_date(event.received_at)+timedelta(days=10))
    upcoming = next((s for s in sessions if s.open >= event.received_at), None)
    return upcoming is not None and now <= upcoming.open + timedelta(minutes=5)


def configured():
    return os.getenv("EVENT_PAPER_ENABLED") == "true" and os.getenv("EVENT_EXCLUSIVE_ACCOUNT") == "true"


class PaperExecutor:
    def __init__(self, client, store, hold=15, limits=None):
        if hold not in {5, 15, 30, 60, 390}:
            raise ValueError("unsupported holding period")
        self.client, self.store, self.hold = client, store, hold
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._lock = threading.RLock()
        self._verified = float("-inf")

    def request(self, method, path, **kwargs):
        check_environment()
        url = PAPER_TRADING_URL + "/v2/" + path
        assert_paper_url(url)
        # Every write re-verifies the paper account; reads may reuse a check from the last few seconds.
        if method != "GET" or time.monotonic() - self._verified > 3:
            self.client.get_paper_account()
            self._verified = time.monotonic()
        return self.client._request(method, url, **kwargs)

    def intents(self):
        with self.store.storage.tx() as conn:
            return [(key, json.loads(payload)) for key, payload in conn.execute("SELECT * FROM event_orders")]

    def open_intents(self, strategy=None):
        return [i for _, i in self.intents() if i["state"] != "closed"
                and strategy in (None, i.get("strategy", "news"))]

    def save(self, key, intent):
        with self.store.storage.tx() as conn:
            conn.execute("UPDATE event_orders SET payload=? WHERE event_key=?", (json.dumps(intent), key))

    def find_order(self, client_id):
        try:
            order = self.request("GET", "orders:by_client_order_id", params={"client_order_id": client_id})
            if order.get("order_class") in {"bracket", "oto"} and not order.get("legs"):
                order = self.request("GET", "orders/" + order["id"], params={"nested": "true"})
            if order.get("status") == "replaced":
                raise RuntimeError("Order replaced outside this executor; manual reconciliation required")
            return order
        except AlpacaAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def request_exit(self, strategy, symbol, reason):
        """Ask the execution monitor to close a strategy's position at the market."""
        with self._lock:
            for key, intent in self.intents():
                if (intent["state"] != "closed" and intent.get("strategy", "news") == strategy
                        and intent["symbol"] == symbol and not intent.get("exit_requested")):
                    intent["exit_requested"] = reason
                    self.save(key, intent)

    def maintain(self, now=None):
        with self._lock:
            return self._maintain(now or datetime.now(UTC))

    def _maintain(self, now):
        if not configured():
            raise RuntimeError("paper maintenance requires enabled exclusive-account mode")
        killed = Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL")).exists()
        found, missing = [], False
        for key, intent in self.intents():
            if intent["state"] == "closed":
                continue
            if intent.get("holding"):  # filled entry with no working orders: only the position matters
                order = dict(status="filled", qty=intent["qty"], filled_qty=intent["qty"])
            else:
                order = self.find_order(intent["client_id"])
                if order is None:
                    # A reservation may have reached the broker despite a timeout. Never guess/resubmit.
                    # The other intents are still managed below, so their exits are not held up by it.
                    missing = True
                    continue
            found.append((key, intent, order))
        # Read positions after the orders, so a fill seen above is never missing from this snapshot.
        positions = {p["symbol"]: p for p in self.request("GET", "positions")} if found else {}
        active = []
        for key, intent, order in found:
            intent["broker_status"] = order["status"]
            intent["filled_qty"] = order.get("filled_qty", "0")
            owned = positions.get(intent["symbol"])
            orders = [order] + (order.get("legs") or [])
            pending = [o for o in orders if o["status"] not in TERMINAL]
            filled = float(order.get("filled_qty", 0))
            if not owned and not pending:
                # A fill can precede its position by a moment; wait before treating the intent as flat.
                filled_at = order.get("filled_at")
                if not (filled and filled_at and now - datetime.fromisoformat(
                        filled_at.replace("Z", "+00:00")) < timedelta(seconds=30)):
                    intent["state"] = "closed"
                self.save(key, intent)
                if intent["state"] != "closed":
                    active.append(intent)
                continue
            active.append(intent)
            side = intent.get("side", "long")
            if owned and (owned.get("side") != side or not 0 < abs(float(owned["qty"])) <= intent["qty"] + 1e-9):
                raise RuntimeError("Position ownership/quantity mismatch; manual reconciliation required")
            intent["holding"] = bool(owned and not pending and order["status"] == "filled")
            entry_expired = order["status"] != "filled" and now >= datetime.fromisoformat(
                intent.get("entry_deadline") or intent["deadline"])
            partial = 0 < filled < float(order["qty"])
            deadline = intent.get("deadline")  # None: held until the strategy requests an exit
            exit_due = (killed or partial or entry_expired or bool(intent.get("exit_requested"))
                        or bool(deadline and now >= datetime.fromisoformat(deadline)))
            if exit_due:
                intent["state"] = "exiting"
                # Cancel only this intent's entry and bracket legs; wait for confirmed cancellation.
                for pending_order in pending:
                    if pending_order["status"] != "pending_cancel":
                        self.request("DELETE", "orders/" + pending_order["id"])
                if not pending and owned and get_calendar("NASDAQ").session_for(now):
                    clock = self.request("GET", "clock")
                    if clock.get("is_open"):
                        exit_id = "exit-" + key[:40]
                        exit_order = self.find_order(exit_id)
                        if exit_order is None:
                            if intent.get("exit_reserved"):
                                raise RuntimeError("Exit response is ambiguous; manual reconciliation required")
                            intent["exit_reserved"] = True
                            self.save(key, intent)
                            self.request("POST", "orders", json=dict(symbol=intent["symbol"],
                                qty=owned["qty"].lstrip("-"), side="sell" if side == "long" else "buy",
                                type="market", time_in_force="day", extended_hours=False,
                                client_order_id=exit_id))
                        elif exit_order["status"] in TERMINAL and any(
                                p["symbol"] == intent["symbol"] for p in self.request("GET", "positions")):
                            raise RuntimeError("Exit terminated with residual position; manual reconciliation required")
            self.save(key, intent)
        if missing:
            raise RuntimeError("Reserved entry not found at broker; manual reconciliation required")
        return active

    def _blocked(self):
        if not configured():
            return "paper execution disabled (requires explicit exclusive-account configuration)"
        if not self.store.storage.get("event-paper-armed"):
            return "paper entries paused in dashboard"
        if Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL")).exists():
            return "kill switch"
        return None

    def enter(self, event, decision, quote, now=None):
        """Submit the news strategy's bracket order for a fresh event with a directional AI call."""
        with self._lock:
            result = self._enter(event, decision, quote, now or datetime.now(UTC))
            self.store.storage.put("event-last-entry-check", dict(at=datetime.now(UTC).isoformat(),
                event=event.key, ticker=event.ticker, result=result))
            return result

    def _enter(self, event, decision, quote, now):
        blocked = self._blocked()
        if blocked:
            return blocked
        decision.validate_event(event)
        direction = decision.side()
        if event.mode != "forward" or direction == 0:
            return "abstain: no directional call"
        if not event_is_fresh(event, now):
            return "stale or unavailable event"
        signal = Signal("news", event.key, event.ticker, direction, stop_pct=.01, target_r=2,
                        hold_minutes=self.hold, fresh=lambda at: event_is_fresh(event, at))
        return self._submit([signal], {event.ticker: quote}, now)[event.key]

    def submit(self, signals, quotes, now=None):
        """Send each signal to Alpaca Paper as a real order. Returns {signal key: result}."""
        with self._lock:
            return self._submit(list(signals), quotes, now or datetime.now(UTC))

    def _submit(self, signals, quotes, now):
        started = time.monotonic()

        def every(result):
            return {signal.key: result for signal in signals}

        blocked = self._blocked()
        if blocked:
            return every(blocked)
        session = get_calendar("NASDAQ").session_for(now)
        if not session or session.close - now < timedelta(minutes=5):
            return every("outside Nasdaq regular entry window")
        clock = self.request("GET", "clock")
        clock_at = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
        if not clock.get("is_open") or abs((now-clock_at).total_seconds()) > 10:
            return every("broker market clock closed or stale")
        active = self._maintain(now)
        # One intent per symbol: maintain() reconciles intents to broker positions by symbol.
        symbols = sorted({signal.symbol for signal in signals})
        occupied = ({i["symbol"] for i in active} | {p["symbol"] for p in self.request("GET", "positions")}
                    | {o["symbol"] for o in self.request("GET", "orders", params={
                        "status": "open", "symbols": ",".join(symbols), "limit": 500})})
        account = self.client.get_paper_account()
        if account.get("status") != "ACTIVE" or account.get("trading_blocked") or account.get("account_blocked"):
            return every("account blocked")
        eq, cash = float(account["equity"]), float(account["cash"])
        last_equity = float(account.get("last_equity", account["equity"]))
        if not all(math.isfinite(value) and value > 0 for value in (eq, cash, last_equity)):
            return every("invalid or insufficient account equity")
        day = str(ny_date(now))
        baseline_key, halt_key = "event-paper-day:" + day, "event-paper-halted:" + day
        baseline = self.store.storage.get(baseline_key)
        if baseline is None:
            baseline = last_equity
            self.store.storage.put(baseline_key, baseline)
        if eq <= baseline - max(0, baseline)*.03:
            self.store.storage.put(halt_key, True)
        if self.store.storage.get(halt_key):
            return every("daily loss limit")
        counts = Counter(i.get("strategy", "news") for i in active)
        buying_power = float(account.get("buying_power", eq))
        results = {}
        for signal in signals:
            execution_now = now + timedelta(seconds=time.monotonic()-started)
            if (session.close-execution_now < timedelta(minutes=5) or abs((execution_now-clock_at).total_seconds()) > 10
                    or (signal.fresh and not signal.fresh(execution_now))):
                results[signal.key] = "entry checks took too long; fresh data required"
                continue
            blocked = self._blocked()  # a pause or emergency stop takes effect in the middle of a batch
            if blocked:
                results[signal.key] = blocked
                continue
            limits = self.limits.get(signal.strategy)
            if limits is None or signal.direction not in (1, -1):
                results[signal.key] = "unknown strategy or direction"
            elif counts[signal.strategy] >= limits["max_positions"]:
                results[signal.key] = "max concurrent positions"
            elif signal.symbol in occupied:
                results[signal.key] = "symbol occupied"
            else:
                result, spent = self._place(signal, quotes.get(signal.symbol), limits, eq, buying_power,
                                            execution_now, session)
                results[signal.key] = result
                if result == "submitted":
                    counts[signal.strategy] += 1
                    occupied.add(signal.symbol)
                    buying_power -= spent
        return results

    def _place(self, signal, quote, limits, eq, buying_power, now, session):
        try:
            timestamp = datetime.fromisoformat(quote["t"].replace("Z", "+00:00"))
            bid, ask = float(quote["bp"]), float(quote["ap"])
        except (KeyError, TypeError, ValueError):
            return "stale/invalid/illiquid quote", 0
        if (not all(math.isfinite(p) for p in (bid, ask)) or not 0 < bid <= ask
                or not 0 <= (now-timestamp).total_seconds() <= 10 or (ask-bid)/ask > .002):
            return "stale/invalid/illiquid quote", 0
        direction = signal.direction
        limit = round(ask * 1.001, 2) if direction == 1 else round(bid * .999, 2)
        stop = signal.stop
        if signal.stop_pct is not None:
            stop = limit * (1 - direction*signal.stop_pct)
        pct = limits["position_pct"] if signal.position_pct is None else min(limits["position_pct"], signal.position_pct)
        size = min(eq*pct/100/limit, buying_power*.9/limit)
        if stop is not None:
            stop = round(stop, 2)
            # Alpaca rejects a stop on the wrong side of, or within a cent of, the entry price.
            if not math.isfinite(stop) or stop <= 0 or (limit-stop)*direction < max(.02, limit*.001):
                return "size below one share or invalid stop", 0
            size = min(size, eq*limits["risk_pct"]/100/(abs(limit-stop) + limit*.001 + .02))
        fractional = signal.fractional and direction == 1 and stop is None
        qty = math.floor(size*1000)/1000 if fractional else math.floor(size)
        if qty*limit < 1 or (qty < 1 and not fractional):
            return "size below one share or invalid stop", 0
        order = dict(symbol=signal.symbol, qty=str(qty), side="buy" if direction == 1 else "sell", type="limit",
                     limit_price=str(limit), time_in_force="day", extended_hours=False)
        if stop is not None:
            order.update(order_class="oto", stop_loss={"stop_price": str(stop)})
            if signal.target_r:
                order.update(order_class="bracket",
                             take_profit={"limit_price": str(round(limit+signal.target_r*(limit-stop), 2))})
        deadline = None
        if signal.intraday:  # flat before the close, sooner when the strategy sets a holding period
            deadline = session.close - timedelta(minutes=5)
            if signal.hold_minutes:
                deadline = min(now+timedelta(minutes=signal.hold_minutes), session.close-timedelta(minutes=2))
            deadline = deadline.isoformat()
        client_id = "evt-" + signal.key[:40]
        intent = dict(state="reserved", strategy=signal.strategy, client_id=client_id, symbol=signal.symbol,
            qty=qty, side="long" if direction == 1 else "short", limit=limit, stop=stop,
            opened=now.isoformat(), deadline=deadline, entry_deadline=(now+timedelta(seconds=30)).isoformat())
        with self.store.storage.tx() as conn:
            inserted = conn.execute("INSERT OR IGNORE INTO event_orders VALUES (?,?)",
                                    (signal.key, json.dumps(intent))).rowcount
        if not inserted:
            return "duplicate event", 0
        try:
            self.request("POST", "orders", json=dict(order, client_order_id=client_id))
        except AlpacaAPIError as exc:
            if exc.status_code in REJECTED:
                intent.update(state="closed", broker_status="rejected", http_status=exc.status_code,
                              error=str(exc)[-200:])
                self.save(signal.key, intent)
                return "broker rejected entry; inspect journal", 0
            raise
        intent["state"] = "submitted"
        self.save(signal.key, intent)
        return "submitted", qty*limit
