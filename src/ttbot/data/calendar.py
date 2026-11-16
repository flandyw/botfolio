"""Exchange-calendar helpers (NYSE regular hours, holidays, early closes)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

from ttbot.data.models import Bar


@dataclass(frozen=True, slots=True)
class Session:
    day: date
    open: datetime  # UTC
    close: datetime  # UTC


class MarketCalendar:
    """Thin cached wrapper around pandas-market-calendars."""

    def __init__(self, name: str = "XNYS") -> None:
        self._cal = mcal.get_calendar(name)
        self._cache: dict[date, Session | None] = {}
        self._loaded: set[int] = set()

    def _load_year(self, year: int) -> None:
        if year in self._loaded:
            return
        sched = self._cal.schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
        d = date(year, 1, 1)
        while d.year == year:
            self._cache[d] = None
            d += timedelta(days=1)
        for idx, row in sched.iterrows():
            day = pd.Timestamp(idx).date()
            self._cache[day] = Session(
                day=day,
                open=row["market_open"].to_pydatetime(),
                close=row["market_close"].to_pydatetime(),
            )
        self._loaded.add(year)

    def session(self, day: date) -> Session | None:
        self._load_year(day.year)
        return self._cache.get(day)

    def session_for(self, ts: datetime) -> Session | None:
        """Regular session containing UTC instant ``ts`` (open <= ts < close), else None."""
        ny_day = pd.Timestamp(ts).tz_convert("America/New_York").date()
        s = self.session(ny_day)
        if s is None or not (s.open <= ts < s.close):
            return None
        return s

    def sessions_between(self, start: date, end: date) -> list[Session]:
        out = []
        d = start
        while d <= end:
            s = self.session(d)
            if s:
                out.append(s)
            d += timedelta(days=1)
        return out

    def is_open(self, ts: datetime) -> bool:
        return self.session_for(ts) is not None

    def annotate(self, bar: Bar) -> Bar | None:
        """Attach session fields; returns None for bars outside regular trading hours."""
        s = self.session_for(bar.ts)
        if s is None:
            return None
        end = bar.ts + timedelta(minutes=bar.timeframe)
        return Bar(
            symbol=bar.symbol,
            ts=bar.ts,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            timeframe=bar.timeframe,
            session=s.day,
            minute_of_session=int((bar.ts - s.open).total_seconds() // 60),
            minutes_to_close=int((s.close - end).total_seconds() // 60),
            is_last=end >= s.close,
        )


def ny_date(ts: datetime) -> date:
    """Calendar date in New York for a tz-aware instant."""
    return pd.Timestamp(ts).tz_convert("America/New_York").date()


@lru_cache(maxsize=4)
def get_calendar(name: str = "XNYS") -> MarketCalendar:
    return MarketCalendar(name)


def frame_to_bars(df: pd.DataFrame, calendar: MarketCalendar) -> list[Bar]:
    """Convert a DataFrame (symbol, ts, open, high, low, close, volume) into session-annotated 1m bars,
    dropping extended-hours bars. Output is sorted by (ts, symbol)."""
    if df.empty:
        return []
    df = df.sort_values(["ts", "symbol"])
    out: list[Bar] = []
    for row in df.itertuples(index=False):
        b = calendar.annotate(
            Bar(
                symbol=str(row.symbol),
                ts=pd.Timestamp(row.ts).to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
        )
        if b is not None:
            out.append(b)
    return out
