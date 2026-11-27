"""Scores each AI news call against the minute prices that followed it, for the news page's call review."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from botfolio.data.calendar import get_calendar, ny_date
from botfolio.events.models import Decision, Event

NY = ZoneInfo("America/New_York")
STOP, TARGET = .02, None  # the news strategy's stop; it sets no target


def chart_time(ts: datetime) -> int:
    """New York wall-clock time as fake-UTC seconds, the convention the web charts use."""
    return int(ts.astimezone(NY).replace(tzinfo=UTC).timestamp())


def calls(store) -> list[dict]:
    """Each article's first AI decision that called a direction or held one back as priced in, with its session."""
    with store.storage.tx() as conn:
        rows = conn.execute("SELECT d.event_key, d.payload, d.model, e.payload FROM event_decisions d "
                            "JOIN events e ON e.event_key = d.event_key ORDER BY d.completed_at").fetchall()
        traded = {k for k, p in conn.execute("SELECT event_key, payload FROM event_orders")
                  if float(json.loads(p).get("filled_qty") or 0) > 0}
    cal, seen, out = get_calendar("NASDAQ"), set(), []
    for key, dp, model, ep in rows:
        if key in seen:
            continue
        seen.add(key)
        d, e = Decision.model_validate_json(dp), Event.model_validate_json(ep)
        held = (d.recommended_action == "HOLD" and d.materiality in ("high", "medium")
                and d.directional_hypothesis in ("bullish", "bearish"))
        if not d.side() and not held:
            continue
        sessions = cal.sessions_between(ny_date(e.received_at), ny_date(e.received_at) + timedelta(days=10))
        session = next((s for s in sessions if s.close > e.received_at), None)
        if session is None:
            continue
        out.append(dict(key=key, ticker=e.ticker, day=str(session.day), open=session.open, close=session.close,
            published=e.published_at, received=e.received_at, source=e.source.removeprefix("rss:"), url=e.source_url,
            text=e.text[:600], side=d.side() or (1 if d.directional_hypothesis == "bullish" else -1),
            held=not d.side(), action=d.recommended_action, materiality=d.materiality, model=model,
            reasoning=d.reasoning, evidence=d.key_evidence[:1], traded=key in traded))
    return out


def score(call: dict, times: list[datetime], closes: list[float]) -> dict | None:
    """Entry is the first price after the bot saw the article; moves are signed so a correct call is positive."""
    i = next((k for k, t in enumerate(times) if t >= call["received"]), None)
    if i is None:
        return None
    entry = closes[i]

    def at(minutes):
        j = i
        while j + 1 < len(times) and times[j + 1] <= times[i] + timedelta(minutes=minutes):
            j += 1
        return j

    def move(j):
        return call["side"] * (closes[j] / entry - 1)

    j15, j60 = at(15), at(60)
    bracket = "time"
    for j in range(i + 1, j60 + 1):
        if move(j) <= -STOP:
            bracket = "stop"
            break
        if TARGET is not None and move(j) >= TARGET:
            bracket = "target"
            break
    m60 = move(j60)
    verdict = "flat" if abs(m60) < .001 else "right" if (m60 > 0) != call["held"] else "wrong"
    return dict(entry_time=chart_time(times[i]), entry=entry, after60_time=chart_time(times[j60]),
                after60=closes[j60], m15=move(j15), m60=m60, m_close=move(len(closes) - 1),
                full_hour=times[j60] - times[i] >= timedelta(minutes=59), bracket=bracket, verdict=verdict)


def review(store, client, feed: str, day: str | None = None) -> dict:
    """The calls acted on in one session (default: the latest), scored, with that session's minute prices."""
    everything = calls(store)
    now = datetime.now(UTC)
    days = sorted({c["day"] for c in everything if c["open"] <= now}, reverse=True)
    day = day if day in days else (days[0] if days else None)
    todays = [c for c in everything if c["day"] == day]
    if not todays:
        return dict(days=days, day=day, calls=[], prices={})
    open_, close = todays[0]["open"], todays[0]["close"]
    symbols = sorted({c["ticker"] for c in todays})
    end = min(close, now)
    prices = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        bars = client.get_bars(batch, open_ - timedelta(hours=1, minutes=30), end, "1Min", feed, "raw")
        daily = client.get_bars(batch, open_ - timedelta(days=7), open_ - timedelta(hours=6), "1Day", feed, "raw")
        for symbol, g in bars.groupby("symbol"):
            g = g.sort_values("ts")
            prev = daily[daily.symbol == symbol].sort_values("ts")
            prices[symbol] = dict(times=[t.to_pydatetime() for t in g.ts], closes=[float(v) for v in g.close],
                                  prev_close=float(prev.close.iloc[-1]) if len(prev) else None)
    out = []
    for c in todays:
        p = prices.get(c["ticker"])
        s = p and score(c, p["times"], p["closes"])
        if not s:
            continue
        out.append({**{k: v for k, v in c.items() if k not in ("open", "close")}, **s,
                    "published": c["published"].isoformat(), "received": c["received"].isoformat(),
                    "published_time": chart_time(c["published"])})
    out.sort(key=lambda c: c["received"], reverse=True)
    return dict(days=days, day=day, open=chart_time(open_), close=chart_time(close), calls=out,
                prices={s: dict(time=[chart_time(t) for t in p["times"]], close=p["closes"], prev_close=p["prev_close"])
                        for s, p in prices.items() if any(c["ticker"] == s for c in out)})
