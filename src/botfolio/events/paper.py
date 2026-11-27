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
from botfolio.events import risk
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
    extended: bool = False          # may enter pre-market/after-hours (simple limit; stop/target enforced here)
    entry_slip: float = .001        # entry limit this far through the quote
    context: dict | None = None     # strategy values at the signal, for the activity log


# Replaying the AI's calls of 2026-11-17..25: news breaking 09:30-09:45 lost entered at once (-3.5%, 20 trades) and
# made +11.8% waiting for 09:45; news from outside regular hours lost 0.75% a trade entered
# pre-market (13 trades, 5 of 6 days down) and made +0.18-0.32% a trade entered at the 09:30 open (80 trades).
OPENING_WAIT = timedelta(minutes=15)
# Fills of 2026-11-23..25 (35 trades): calls whose ask+0.1% limit went unfilled in 30s beat SPY by 59bp over the next
# hour, filled ones by 4bp, so entries cross further; 1% stops were the whole loss and the 2R target filled once, so
# the stop is wider with no target; medium-materiality calls and Yahoo headlines (stories about moves already made) lost.
NEWS_STOP, NEWS_SLIP = .02, .004


def opening_wait(now):
    """True during the first 15 minutes of the regular session, when news entries wait."""
    s = get_calendar("NASDAQ").session_for(now)
    return bool(s and now < s.open + OPENING_WAIT)


def entry_ready(event, now):
    """News trades only in regular hours, and news breaking in the first 15 minutes waits for 09:45."""
    return bool(get_calendar("NASDAQ").session_for(now)) and not (opening_wait(now) and opening_wait(event.received_at))


def event_is_fresh(event, now):
    # Downloading an old article must never renew its trading eligibility.
    # Company press-release feeds stamp items a few minutes before they appear (NVIDIA's: ~6 min).
    lag = 10 if event.source.startswith("rss:") and "yahoo" not in event.source else 5
    if event.mode != "forward" or now < event.received_at or event.received_at-event.content_at > timedelta(minutes=lag):
        return False
    cal = get_calendar("NASDAQ")
    s = cal.session_for(event.received_at)
    if s:
        return now <= max(event.received_at, s.open + OPENING_WAIT) + timedelta(minutes=5)
    # Outside regular hours the news waits for the next regular open.
    sessions = cal.sessions_between(ny_date(event.received_at), ny_date(event.received_at)+timedelta(days=10))
    upcoming = next((s for s in sessions if s.open >= event.received_at), None)
    return upcoming is not None and now <= upcoming.open + timedelta(minutes=5)


def configured():
    return os.getenv("EVENT_PAPER_ENABLED") == "true" and os.getenv("EVENT_EXCLUSIVE_ACCOUNT") == "true"


class PaperExecutor:
    def __init__(self, client, store, hold=15, limits=None, feed="iex"):
        if hold not in {5, 15, 30, 60, 390}:
            raise ValueError("unsupported holding period")
        self.client, self.store, self.hold, self.feed = client, store, hold, feed
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

    def find_order(self, client_id, *, allow_replaced=False):
        try:
            order = self.request("GET", "orders:by_client_order_id", params={"client_order_id": client_id})
            if order.get("order_class") in {"bracket", "oto"} and not order.get("legs"):
                order = self.request("GET", "orders/" + order["id"], params={"nested": "true"})
            if order.get("status") == "replaced" and not allow_replaced:
                raise RuntimeError("Order replaced outside this executor; manual reconciliation required")
            return order
        except AlpacaAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def _exit_price(self, symbol, side, owned, now):
        """Where a close would trade: the bid for a long, the ask for a short; the position's last price if stale."""
        quote = self.client.get_latest_quotes([symbol], self.feed).get(symbol) or {}
        try:
            bid, ask = float(quote["bp"]), float(quote["ap"])
            # IEX alone often quotes a wide market (MSFT bid 460.75 while trading at 483 on 2026-11-25, a false
            # stop): a spread over 0.5% is not a price, so fall back to the last trade.
            if (0 < bid <= ask and (ask-bid)/ask <= .005
                    and now - datetime.fromisoformat(quote["t"].replace("Z", "+00:00")) <= timedelta(seconds=60)):
                return bid if side == "long" else ask
        except (KeyError, TypeError, ValueError):
            pass
        return float(owned["current_price"])

    def _cancel(self, order):
        if order["status"] == "pending_cancel":
            return
        try:
            self.request("DELETE", "orders/" + order["id"])
        except AlpacaAPIError as exc:
            if exc.status_code != 422:  # 422: already cancelling or filled; the next pass sees its final status
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
            # A partial entry with a stop keeps its shares: cancel the rest (and any legs) and enforce the
            # stop/target from the quote. Closing it at once threw away the best trades on 2026-11-24.
            keep = partial and intent.get("stop") is not None
            if keep:
                intent.setdefault("soft_stop", intent["stop"])
                intent.setdefault("soft_target", intent.get("target"))
                for pending_order in pending:
                    self._cancel(pending_order)
            if intent.get("soft_stop") and owned and not pending and not intent.get("exit_requested"):
                # Extended-hours entries carry no broker stop/target legs; enforce them from the quote.
                price, sign = self._exit_price(intent["symbol"], side, owned, now), 1 if side == "long" else -1
                target = intent.get("soft_target")
                if (price-intent["soft_stop"])*sign <= 0 or (target is not None and (price-target)*sign >= 0):
                    intent["exit_requested"] = f"stop/target reached at {price}"
            exit_due = (killed or ((partial or entry_expired) and not keep) or bool(intent.get("exit_requested"))
                        or bool(deadline and now >= datetime.fromisoformat(deadline)))
            if exit_due:
                intent["state"] = "exiting"
                # Cancel only this intent's entry and bracket legs; wait for confirmed cancellation.
                for pending_order in pending:
                    self._cancel(pending_order)
                cal = get_calendar("NASDAQ")
                if not pending and owned and cal.extended_session_for(now):
                    clock = self.request("GET", "clock")
                    regular = bool(clock.get("is_open") and cal.session_for(now))
                    if regular or intent.get("soft_stop"):  # only extended-hours entries exit outside regular hours
                        exit_id = "exit-" + key[:40]
                        exit_order = self.find_order(exit_id)
                        if exit_order is None:
                            if intent.get("exit_reserved"):
                                raise RuntimeError("Exit response is ambiguous; manual reconciliation required")
                            intent["exit_reserved"] = True
                            self.save(key, intent)
                            order = dict(symbol=intent["symbol"], qty=owned["qty"].lstrip("-"),
                                         side="sell" if side == "long" else "buy", time_in_force="day",
                                         type="market", extended_hours=False, client_order_id=exit_id)
                            if not regular:
                                # Extended hours take limit orders only: cross the quote by 1% to fill like a market order.
                                # shortcut: an exit the price outruns by >1% waits unfilled; add cancel/replace if it happens.
                                price = self._exit_price(intent["symbol"], side, owned, now)
                                order.update(type="limit", extended_hours=True,
                                             limit_price=str(round(price*(.99 if side == "long" else 1.01), 2)))
                            self.request("POST", "orders", json=order)
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
        if decision.materiality != "high":
            return "abstain: not high materiality"
        if "yahoo" in event.source:
            return "abstain: Yahoo headlines trail the move"
        if not event_is_fresh(event, now):
            return "stale or unavailable event"
        session = get_calendar("NASDAQ").session_for(now)
        if session and session.close - now < timedelta(minutes=min(self.hold, 60)):
            return "too close to the close for the holding period"
        day = ny_date(now)
        if any(i.get("strategy", "news") == "news" and i["symbol"] == event.ticker and "opened" in i
               and float(i.get("filled_qty") or 0) > 0 and ny_date(datetime.fromisoformat(i["opened"])) == day
               for _, i in self.intents()):
            return "symbol already traded today"  # repeat/reversed calls on one name lost money on 2026-11-24
        if not entry_ready(event, now):
            return "waiting for the open or the opening range"
        signal = Signal("news", event.key, event.ticker, direction, stop_pct=NEWS_STOP, entry_slip=NEWS_SLIP,
                        hold_minutes=self.hold, fresh=lambda at: event_is_fresh(event, at),
                        context=dict(at=event.content_at.isoformat(), note=decision.reasoning,
                                     metrics={"AI action": decision.recommended_action}))
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
        cal = get_calendar("NASDAQ")
        session = cal.session_for(now)
        extended_hours = session is None and all(signal.extended for signal in signals)
        if extended_hours:
            session = cal.extended_session_for(now)
        if not session or session.close - now < timedelta(minutes=5):
            return every("outside Nasdaq entry window")
        clock = self.request("GET", "clock")
        clock_at = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
        if not (clock.get("is_open") or extended_hours) or abs((now-clock_at).total_seconds()) > 10:
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
        eq = float(account["equity"])
        last_equity = float(account.get("last_equity", account["equity"]))
        buying_power = float(account.get("buying_power", eq))
        # Not cash: cash_park keeps cash near zero, so one fill made it negative and blocked every later entry.
        if not all(math.isfinite(value) and value > 0 for value in (eq, buying_power, last_equity)):
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
            if limits and signal.strategy in risk.SCALED:  # Claude's daily risk regime scales the max size
                regime = risk.scale(self.store.storage, now)
                limits = {**limits, "position_pct": limits["position_pct"]*regime, "risk_pct": limits["risk_pct"]*regime}
            if limits is None or signal.direction not in (1, -1):
                results[signal.key] = "unknown strategy or direction"
            elif counts[signal.strategy] >= limits["max_positions"]:
                results[signal.key] = "max concurrent positions"
            elif signal.symbol in occupied:
                results[signal.key] = "symbol occupied"
            else:
                result, spent = self._place(signal, quotes.get(signal.symbol), limits, eq, buying_power,
                                            execution_now, session, extended_hours)
                results[signal.key] = result
                if result == "submitted":
                    counts[signal.strategy] += 1
                    occupied.add(signal.symbol)
                    buying_power -= spent
        return results

    def _place(self, signal, quote, limits, eq, buying_power, now, session, extended_hours=False):
        try:
            timestamp = datetime.fromisoformat(quote["t"].replace("Z", "+00:00"))
            bid, ask = float(quote["bp"]), float(quote["ap"])
        except (KeyError, TypeError, ValueError):
            return "stale/invalid/illiquid quote", 0
        if (not all(math.isfinite(p) for p in (bid, ask)) or not 0 < bid <= ask
                or not 0 <= (now-timestamp).total_seconds() <= 10 or (ask-bid)/ask > (.005 if extended_hours else .002)):
            return "stale/invalid/illiquid quote", 0
        direction = signal.direction
        limit = round(ask*(1+signal.entry_slip), 2) if direction == 1 else round(bid*(1-signal.entry_slip), 2)
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
                     limit_price=str(limit), time_in_force="day", extended_hours=extended_hours)
        target = round(limit+signal.target_r*(limit-stop), 2) if stop is not None and signal.target_r else None
        if stop is not None and not extended_hours:  # Alpaca refuses bracket/OTO legs outside regular hours
            order.update(order_class="oto", stop_loss={"stop_price": str(stop)})
            if target is not None:
                order.update(order_class="bracket", take_profit={"limit_price": str(target)})
        deadline = None
        if signal.intraday:  # flat before the close, sooner when the strategy sets a holding period
            deadline = session.close - timedelta(minutes=5)
            if signal.hold_minutes:
                deadline = min(now+timedelta(minutes=signal.hold_minutes), session.close-timedelta(minutes=2))
            deadline = deadline.isoformat()
        client_id = "evt-" + signal.key[:40]
        intent = dict(state="reserved", strategy=signal.strategy, client_id=client_id, symbol=signal.symbol,
            qty=qty, side="long" if direction == 1 else "short", limit=limit, stop=stop, target=target,
            opened=now.isoformat(), deadline=deadline, entry_deadline=(now+timedelta(seconds=30)).isoformat())
        if signal.context:
            intent["context"] = signal.context
        if extended_hours and stop is not None:
            intent.update(soft_stop=stop, soft_target=target)  # enforced by _maintain from the quote
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
