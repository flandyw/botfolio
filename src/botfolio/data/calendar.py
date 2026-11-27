"""Exchange-calendar helpers (NYSE regular hours, holidays, early closes)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

from botfolio.data.models import Bar


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

    def extended_session_for(self, ts: datetime) -> Session | None:
        """The tradeable extended window (see ``extended``) containing ``ts``, else None."""
        s = self.session(pd.Timestamp(ts).tz_convert("America/New_York").date())
        s = s and extended(s)
        return s if s and s.open <= ts < s.close else None

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


def extended(s: Session) -> Session:
    """The extended window the bot can price: IEX quotes from 08:00 ET (regular open is always 09:30 ET) to the close.

    shortcut: Alpaca accepts orders 04:00-20:00 ET, but IEX (the only feed this account has) quotes nothing
    outside 08:00-16:00; widen to open-5h30m / close+4h with a SIP subscription.
    """
    return Session(s.day, s.open - timedelta(hours=1, minutes=30), s.close)


def ny_date(ts: datetime) -> date:
    """Calendar date in New York for a tz-aware instant."""
    return pd.Timestamp(ts).tz_convert("America/New_York").date()


@lru_cache(maxsize=4)
def get_calendar(name: str = "XNYS") -> MarketCalendar:
    return MarketCalendar(name)


def frame_to_bars(df: pd.DataFrame, calendar: MarketCalendar) -> list[Bar]:
    """Return regular-session minute bars in timestamp and symbol order."""
    if df.empty:
        return []
    out: list[Bar] = []
    for row in df.sort_values(["ts", "symbol"]).itertuples(index=False):
        ts = pd.Timestamp(row.ts).to_pydatetime()
        if calendar.session_for(ts):
            out.append(Bar(
                symbol=str(row.symbol),
                ts=ts,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            ))
    return out
