"""Core market-data types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True, slots=True)
class Bar:
    """An OHLCV bar. ``ts`` is the bar START time (UTC, tz-aware), matching Alpaca's convention.

    A bar is only knowable at ``end`` (= ts + timeframe). Session fields are derived from the exchange
    calendar (clock-based, so they never use future data).
    """

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: int = 1  # minutes
    session: date | None = None
    minute_of_session: int = 0  # minutes from the session open to the bar START
    minutes_to_close: int = 0  # minutes from the bar END to the session close
    is_last: bool = False  # bar ends at (or after) the session close

    @property
    def end(self) -> datetime:
        return self.ts + timedelta(minutes=self.timeframe)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open
