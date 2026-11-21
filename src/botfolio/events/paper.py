"""Paper-only execution with durable intents, bounded entries and broker reconciliation."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from botfolio.data.alpaca import AlpacaAPIError
from botfolio.data.calendar import get_calendar, ny_date
from botfolio.safety import PAPER_TRADING_URL, assert_paper_url, check_environment

TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
MAX_POSITIONS = 5  # 5 x 25% notional keeps gross exposure near 125% of equity


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
    def __init__(self, client, store, hold=15):
        if hold not in {5, 15, 30, 60, 390}:
            raise ValueError("unsupported holding period")
        self.client, self.store, self.hold = client, store, hold
        self._lock = threading.RLock()

    def request(self, method, path, **kwargs):
        check_environment()
        url = PAPER_TRADING_URL + "/v2/" + path
        assert_paper_url(url)
        self.client.get_paper_account()
        return self.client._request(method, url, **kwargs)

    def intents(self):
        with self.store.storage.tx() as conn:
            return [(key, json.loads(payload)) for key, payload in conn.execute("SELECT * FROM event_orders")]

    def save(self, key, intent):
        with self.store.storage.tx() as conn:
            conn.execute("UPDATE event_orders SET payload=? WHERE event_key=?", (json.dumps(intent), key))

    def find_order(self, client_id):
        try:
            order = self.request("GET", "orders:by_client_order_id", params={"client_order_id": client_id})
            if order.get("order_class") == "bracket":
                order = self.request("GET", "orders/" + order["id"], params={"nested": "true"})
            if order.get("status") == "replaced":
                raise RuntimeError("Order replaced outside this executor; manual reconciliation required")
            return order
        except AlpacaAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def maintain(self, now=None):
        with self._lock:
            return self._maintain(now or datetime.now(UTC))

    def _maintain(self, now):
        if not configured():
            raise RuntimeError("paper maintenance requires enabled exclusive-account mode")
        killed = Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL")).exists()
        active = []
        for key, intent in self.intents():
            if intent["state"] == "closed":
                continue
            order = self.find_order(intent["client_id"])
            if order is None:
                # A reservation may have reached the broker despite a timeout. Never guess/resubmit.
                raise RuntimeError("Reserved entry not found at broker; manual reconciliation required")
            intent["broker_status"] = order["status"]
            intent["filled_qty"] = order.get("filled_qty", "0")
            positions = self.request("GET", "positions")
            owned = next((p for p in positions if p["symbol"] == intent["symbol"]), None)
            orders = [order] + (order.get("legs") or [])
            pending = [o for o in orders if o["status"] not in TERMINAL]
            filled = float(order.get("filled_qty", 0))
            if not owned and not pending:
                intent["state"] = "closed"
                self.save(key, intent)
                continue
            active.append(intent)
            side = intent.get("side", "long")
            if owned and (owned.get("side") != side or not 0 < abs(float(owned["qty"])) <= intent["qty"]):
                raise RuntimeError("Position ownership/quantity mismatch; manual reconciliation required")
            entry_expired = order["status"] != "filled" and now >= datetime.fromisoformat(
                intent.get("entry_deadline", intent["deadline"]))
            partial = 0 < filled < float(order["qty"])
            exit_due = killed or partial or entry_expired or now >= datetime.fromisoformat(intent["deadline"])
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
                                qty=str(abs(float(owned["qty"]))), side="sell" if side == "long" else "buy", type="market", time_in_force="day", extended_hours=False,
                                client_order_id=exit_id))
                        elif exit_order["status"] in TERMINAL and owned:
                            raise RuntimeError("Exit terminated with residual position; manual reconciliation required")
            self.save(key, intent)
        return active

    def enter(self, event, decision, quote, now=None):
        with self._lock:
            result = self._enter(event, decision, quote, now or datetime.now(UTC))
            self.store.storage.put("event-last-entry-check", dict(at=datetime.now(UTC).isoformat(),
                event=event.key, ticker=event.ticker, result=result))
            return result

    def _enter(self, event, decision, quote, now):
        started = time.monotonic()
        if not configured():
            return "paper execution disabled (requires explicit exclusive-account configuration)"
        if not self.store.storage.get("event-paper-armed"):
            return "paper entries paused in dashboard"
        if Path(os.getenv("EVENT_KILL_FILE", "data/EVENT_KILL")).exists():
            return "kill switch"
        decision.validate_event(event)
        direction = decision.side()
        if event.mode != "forward" or direction == 0:
            return "abstain: no directional call"
        if not event_is_fresh(event, now):
            return "stale or unavailable event"
        session = get_calendar("NASDAQ").session_for(now)
        if not session or session.close - now < timedelta(minutes=5):
            return "outside Nasdaq regular entry window"
        clock = self.request("GET", "clock")
        clock_at = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
        if not clock.get("is_open") or abs((now-clock_at).total_seconds()) > 10:
            return "broker market clock closed or stale"
        active = self._maintain(now)
        if len(active) >= MAX_POSITIONS:
            return "max concurrent positions"
        # One intent per symbol: maintain() reconciles intents to broker positions by symbol.
        if (any(i["symbol"] == event.ticker for i in active)
            or any(p["symbol"] == event.ticker for p in self.request("GET", "positions"))
            or self.request("GET", "orders", params={"status": "open", "symbols": event.ticker})):
            return "symbol occupied"
        account = self.client.get_paper_account()
        if account.get("status") != "ACTIVE" or account.get("trading_blocked") or account.get("account_blocked"):
            return "account blocked"
        broker_equity, cash = float(account["equity"]), float(account["cash"])
        last_equity = float(account.get("last_equity", account["equity"]))
        if not all(math.isfinite(value) and value > 0 for value in (broker_equity, cash, last_equity)):
            return "invalid or insufficient account equity"
        eq = broker_equity
        day = str(ny_date(now))
        baseline_key, halt_key = "event-paper-day:" + day, "event-paper-halted:" + day
        baseline = self.store.storage.get(baseline_key)
        if baseline is None:
            baseline = last_equity
            self.store.storage.put(baseline_key, baseline)
        if float(account["equity"]) <= baseline - max(0, baseline)*.03:
            self.store.storage.put(halt_key, True)
        if self.store.storage.get(halt_key):
            return "daily loss limit"
        execution_now = now + timedelta(seconds=time.monotonic()-started)
        if (not event_is_fresh(event, execution_now) or session.close-execution_now < timedelta(minutes=5)
            or abs((execution_now-clock_at).total_seconds()) > 10):
            return "entry checks took too long; fresh data required"
        timestamp = datetime.fromisoformat(quote["t"].replace("Z", "+00:00"))
        bid, ask = float(quote["bp"]), float(quote["ap"])
        if (not all(math.isfinite(p) for p in (bid, ask)) or not 0 < bid <= ask
            or not 0 <= (execution_now-timestamp).total_seconds() <= 10 or (ask-bid)/ask > .002):
            return "stale/invalid/illiquid quote"
        if direction == 1:
            limit, stop = round(ask * 1.001, 2), round(ask * 1.001 * .99, 2)
        else:
            limit, stop = round(bid * .999, 2), round(bid * .999 * 1.01, 2)
        risk_per_share = abs(limit-stop) + limit*.001 + .02
        buying_power = float(account.get("buying_power", eq))
        qty = math.floor(min(eq*.01/risk_per_share, eq*.25/limit, buying_power*.9/limit))
        if qty < 1 or stop <= 0 or (stop-limit)*direction >= 0:
            return "size below one share or invalid stop"
        client_id = "evt-" + event.key[:40]
        deadline = min(execution_now+timedelta(minutes=self.hold), session.close-timedelta(minutes=2))
        intent = dict(state="reserved", client_id=client_id, symbol=event.ticker, qty=qty,
            side="long" if direction == 1 else "short",
            deadline=deadline.isoformat(), entry_deadline=(execution_now+timedelta(seconds=30)).isoformat())
        with self.store.storage.tx() as conn:
            inserted = conn.execute("INSERT OR IGNORE INTO event_orders VALUES (?,?)",
                                    (event.key, json.dumps(intent))).rowcount
        if not inserted:
            return "duplicate event"
        try:
            self.request("POST", "orders", json=dict(symbol=event.ticker, qty=str(qty), side="buy" if direction == 1 else "sell", type="limit",
                limit_price=str(limit), time_in_force="day", extended_hours=False, client_order_id=client_id,
                order_class="bracket", stop_loss={"stop_price": str(stop)},
                take_profit={"limit_price": str(round(limit+2*(limit-stop), 2))}))  # 2R either direction
        except AlpacaAPIError as exc:
            if exc.status_code in {400, 401, 403, 404}:
                intent.update(state="closed", broker_status="rejected", http_status=exc.status_code)
                self.save(event.key, intent)
                return "broker rejected entry; inspect journal"
            raise
        intent["state"] = "submitted"
        self.save(event.key, intent)
        return "submitted"
