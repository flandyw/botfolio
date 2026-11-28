"""Read-only broker and market data for a selected journal entry."""

import json
import math
from datetime import UTC, datetime, timedelta

from botfolio.data.calendar import ny_date
from botfolio.events.review import chart_time


def fill(order, kind):
    price, qty = float(order.get("filled_avg_price") or 0), float(order.get("filled_qty") or 0)
    if not (math.isfinite(price) and price > 0 and math.isfinite(qty) and qty > 0):
        return None
    at = order.get("filled_at")
    return dict(kind=kind, price=price, qty=qty, at=at, time=chart_time(datetime.fromisoformat(at)) if at else None)


def trade_activity(executor, row, feed, context, now=None):
    now = now or datetime.now(UTC)
    result = dict(entry=None, exits=[], bars=[], bar_seconds=300, context=context)
    if executor is None:
        result["error"] = "Connect Alpaca Paper to load fills and historical candles."
        return result

    if row.get("client_id"):
        try:
            order = executor.find_order(row["client_id"], allow_replaced=True)
            if order:
                result["status"] = order["status"]
                result["entry"] = fill(order, "Entry")
                for leg in order.get("legs") or []:
                    kind = "Stop loss" if leg.get("type") in {"stop", "stop_limit"} else "Take profit"
                    if execution := fill(leg, kind):
                        result["exits"].append(execution)
            if row.get("exit_reserved"):
                exit_order = executor.find_order("exit-" + row["key"][:40], allow_replaced=True)
                if exit_order and (execution := fill(exit_order, "Strategy exit")):
                    result["exits"].append(execution)
            result["exits"].sort(key=lambda execution: execution["time"] or 0)
        except Exception:
            result["fill_error"] = "Alpaca Paper fills could not be loaded. Try again shortly."

    if not row.get("opened"):
        return result
    opened = datetime.fromisoformat(row["opened"])
    executions = [result["entry"], *result["exits"]]
    execution_dates = [datetime.fromisoformat(e["at"]) for e in executions if e and e["at"]]
    latest = max([opened, *execution_dates])
    if row["state"] != "closed":
        latest = max(latest, now)
    daily = ny_date(latest) != ny_date(opened) and latest - opened > timedelta(days=3)
    result["bar_seconds"] = 86_400 if daily else 300
    start = min([opened, *execution_dates]) - (timedelta(days=7) if daily else timedelta(hours=3))
    if context.get("at"):
        signal_at = datetime.fromisoformat(context["at"])
        if opened - timedelta(days=2) <= signal_at < start:
            start = signal_at - timedelta(minutes=15)
    end = min(now, latest + (timedelta(days=4) if daily else timedelta(hours=4)))
    try:
        bars = executor.client.get_bars([row["symbol"]], start, end, "1Day" if daily else "5Min", feed, "raw")
        records = {
            chart_time(b.ts): dict(time=chart_time(b.ts), open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in bars.itertuples()
        }
        result["bars"] = [records[t] for t in sorted(records)]
    except Exception:
        result["error"] = "Historical candles could not be loaded. Try again shortly."
    return result


def strategy_performance(executor, rows):
    """Gross P&L per strategy from Alpaca Paper fills, matched to the journal by client order id.
    Closed shares are realized; shares still held are marked at the position's current price."""
    opened = [datetime.fromisoformat(r["opened"]) for r in rows if r.get("opened") and r.get("client_id")]
    if not opened:
        return {}
    orders, after = {}, (min(opened) - timedelta(days=1)).isoformat()
    while True:
        page = executor.request("GET", "orders", params=dict(
            status="all", nested="true", limit=500, direction="asc", after=after))
        orders.update((o["client_order_id"], o) for o in page)
        if len(page) < 500:
            break
        after = page[-1]["submitted_at"]
    marks = {p["symbol"]: float(p["current_price"]) for p in executor.request("GET", "positions")}

    result = {}
    for row in rows:
        order = orders.get(row.get("client_id") or "")
        entry = order and fill(order, "Entry")
        sign = {"long": 1, "short": -1}.get(row.get("side"))
        if not entry or not sign:
            continue
        exits = [f for leg in order.get("legs") or [] if (f := fill(leg, "Exit"))]
        if (exit_order := orders.get("exit-" + row["key"][:40])) and (f := fill(exit_order, "Exit")):
            exits.append(f)
        realized = sum((x["price"] - entry["price"]) * x["qty"] for x in exits) * sign
        held = entry["qty"] - sum(x["qty"] for x in exits)
        mark = marks.get(row["symbol"])
        unrealized = (mark - entry["price"]) * held * sign if held > 1e-9 and mark else 0.0
        s = result.setdefault(row.get("strategy", "news"), dict(
            trades=0, closed=0, wins=0, realized=0.0, unrealized=0.0, deployed=0.0))
        s["trades"] += 1
        s["realized"] += realized
        s["unrealized"] += unrealized
        s["deployed"] += entry["price"] * entry["qty"]
        if held <= 1e-9:
            s["closed"] += 1
            s["wins"] += realized > 0
    for s in result.values():
        s["pnl"] = s["realized"] + s["unrealized"]
        s["pnl_pct"] = s["pnl"] / s["deployed"] * 100 if s["deployed"] else None
    return result


class _Snapshot:
    """Answers repeated broker requests from the first response."""
    def __init__(self, executor):
        self.executor, self.seen = executor, {}

    def request(self, method, path, **kwargs):
        key = json.dumps([method, path, kwargs], sort_keys=True)
        if key not in self.seen:
            self.seen[key] = self.executor.request(method, path, **kwargs)
        return self.seen[key]


def news_performance(executor, rows, store):
    """P&L of news trades by the article's source and by the AI model that made the call, so a source or model
    that loses can be switched off on evidence."""
    rows = [r for r in rows if r.get("strategy", "news") == "news"]
    keys = [r["key"] for r in rows]
    marks = ",".join("?" * len(keys))
    with store.storage.tx() as conn:
        source = dict(conn.execute("SELECT event_key, json_extract(payload, '$.source') FROM events "
                                   f"WHERE event_key IN ({marks})", keys))
        # Only a BUY or SELL opens a trade; when several models decided an article, the first such call wins.
        model = dict(conn.execute("SELECT event_key, model FROM event_decisions "
                                  f"WHERE event_key IN ({marks}) AND model != 'none:none' ORDER BY "
                                  "json_extract(payload, '$.recommended_action') IN ('BUY', 'SELL'), completed_at DESC",
                                  keys))
    snapshot = _Snapshot(executor)  # both tables read the same orders and positions
    return {name: strategy_performance(snapshot, [{**r, "strategy": by.get(r["key"], "unknown")} for r in rows])
            for name, by in (("sources", source), ("models", model))}
