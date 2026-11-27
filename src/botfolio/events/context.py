"""Market context available at the time a news event was received."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from botfolio.data.calendar import get_calendar, ny_date

NY = ZoneInfo("America/New_York")


def market_context(bars, event, at):
    prior = [b for b in bars if b.symbol == event.ticker and b.end <= at][-31:]
    if len(prior) < 6 or any(b.ts != a.end for a, b in zip(prior[-6:-1], prior[-5:], strict=True)):
        return {"missing": "insufficient completed pre-decision bars"}
    returns = np.diff(np.log([b.close for b in prior]))
    result = dict(asof=at.isoformat(), last_bar_end=prior[-1].end.isoformat(),
        return_5m=prior[-1].close / prior[-6].close - 1,
        volatility=float(np.std(returns)), volume=prior[-1].volume,
        volume_ratio=prior[-1].volume / max(1, np.mean([b.volume for b in prior[:-1]])),
        market_return=None, sector_return=None, related_announcements=None)
    market = [b for b in bars if b.symbol == "SPY" and b.end <= at][-6:]
    if len(market) == 6:
        result["market_return"] = market[-1].close / market[0].close - 1
    return result


def since_prev_close(snapshots, ticker):
    """Live move since the prior regular close, pre-market included: what the market has already priced in.
    Forward decisions only; on an old article a live snapshot would leak later prices."""
    def move(symbol):
        try:
            s = snapshots[symbol]
            t = s["latestTrade"]["t"]
            at = datetime.fromisoformat(t[:19]).replace(tzinfo=UTC).astimezone(NY)
            day = at.strftime("%Y-%m-%d")
            # Before the open Alpaca's dailyBar is still the last session, so prevDailyBar is two closes back
            # (2026-11-25: NVDA read -11% instead of -5%). Use the latest bar that had closed when the trade printed.
            # shortcut: assumes a 16:00 close; on an early-close day today's bar counts as closed only from 16:00.
            base = next(b for b in (s["dailyBar"], s["prevDailyBar"])
                        if b["t"][:10] < day or (b["t"][:10] == day and at.hour >= 16))
            return s["latestTrade"]["p"] / base["c"] - 1, t
        except (KeyError, TypeError, ValueError, StopIteration, ZeroDivisionError):
            return None, None
    (stock, at), (market, _), (nasdaq, _) = move(ticker), move("SPY"), move("QQQ")
    return dict(since_prev_close=stock, last_trade_at=at, market_since_prev_close=market,
                nasdaq100_since_prev_close=nasdaq)


TRADE = ("BUY or SELL opens a position at the next regular-session price with a 1% stop and a 2% target and closes it "
         "after 60 minutes. News from outside regular hours enters at the 09:30 ET open.")


def news_context(event, events, company=None):
    """What a trader knows besides the article: who the company is, where the session stands, the trade being
    decided, and the stock's earlier headlines from the past day (to judge novelty). All known at receipt."""
    at, cal = event.received_at, get_calendar("NASDAQ")
    s = cal.session_for(at)
    if s:
        session = (f"regular session, {int((at - s.open).total_seconds() // 60)} min after the open, "
                   f"{int((s.close - at).total_seconds() // 60)} min before the close")
    else:
        nxt = next((x for x in cal.sessions_between(ny_date(at), ny_date(at) + timedelta(days=10)) if x.open > at), None)
        session = "market closed" + (f"; next open {nxt.open.astimezone(NY):%a %H:%M} ET, "
                                     f"{(nxt.open - at).total_seconds() / 3600:.1f} h away" if nxt else "")
    recent = sorted((e for e in events if e.ticker == event.ticker and e.text != event.text
                     and at - timedelta(hours=24) <= e.received_at < at), key=lambda e: e.received_at)[-5:]
    return dict(company=company, session=session, trade=TRADE, recent_headlines=[
        dict(at=f"{e.received_at.astimezone(NY):%a %H:%M} ET", headline=e.text[:160]) for e in recent])
