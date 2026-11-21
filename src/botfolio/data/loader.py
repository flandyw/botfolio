"""Historical bar loading with a SQLite cache (only missing sessions are downloaded)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from botfolio.data.alpaca import AlpacaClient
from botfolio.data.calendar import MarketCalendar, Session
from botfolio.storage import Storage

log = logging.getLogger(__name__)


def _chunks(sessions: list[Session], size: int) -> list[list[Session]]:
    out: list[list[Session]] = []
    cur: list[Session] = []
    for s in sessions:
        if cur and (len(cur) >= size or (s.day - cur[-1].day).days > 5):
            out.append(cur)
            cur = []
        cur.append(s)
    if cur:
        out.append(cur)
    return out


def cache_key(feed: str, adjustment: str) -> str:
    """Bar-cache partition: differently adjusted prices must never mix ("all" keeps the original key)."""
    return feed if adjustment == "all" else f"{feed}:{adjustment}"


def load_history(
    client: AlpacaClient | None,
    storage: Storage,
    calendar: MarketCalendar,
    symbols: list[str],
    start: date,
    end: date,
    feed: str = "iex",
    adjustment: str = "all",
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return 1-minute bars for regular sessions in [start, end] (inclusive), downloading what is missing."""
    now = now or datetime.now(UTC)
    sessions = calendar.sessions_between(start, end)
    if not sessions:
        return pd.DataFrame(columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    # SIP on the free plan excludes the latest 15 minutes; only cache sessions that are fully over.
    complete_cutoff = now - timedelta(minutes=20)
    key = cache_key(feed, adjustment)
    for sym in symbols:
        have = storage.fetched_days(sym, key)
        missing = [s for s in sessions if s.day not in have]
        if not missing:
            continue
        if client is None:
            raise RuntimeError(f"{len(missing)} sessions of {sym} not cached and no Alpaca client available")
        for chunk in _chunks(missing, 20):
            t0, t1 = chunk[0].open, min(chunk[-1].close, complete_cutoff)
            if t1 <= t0:
                continue
            log.info("downloading bars", extra={"symbol": sym, "from": str(chunk[0].day), "to": str(chunk[-1].day), "feed": feed})
            df = client.get_bars([sym], t0, t1, "1Min", feed, adjustment)
            done = [(sym, s.day) for s in chunk if s.close <= complete_cutoff]
            storage.save_bars(df, key, done)
    first, last = sessions[0].open, sessions[-1].close
    return storage.load_bars(symbols, key, first, last)
