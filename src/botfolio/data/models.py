"""Core market-data types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Bar:
    """An OHLCV bar. ``ts`` is the bar START time (UTC, tz-aware), matching Alpaca's convention.

    A one-minute bar is only knowable at its end.
    """

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def end(self) -> datetime:
        return self.ts + timedelta(minutes=1)
