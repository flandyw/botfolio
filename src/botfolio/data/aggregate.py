"""Incremental 1-minute -> N-minute bar aggregation, identical in backtest and live modes.

Windows are anchored to the session open (09:30, 09:35, ... ET). A window is emitted as soon as it is
known to be complete: either its final minute arrived, or a later minute arrived (IEX data can skip
minutes with no prints). Partial windows are never emitted early, so there is no repainting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from botfolio.data.models import Bar


@dataclass
class _Window:
    start_minute: int
    bars: list[Bar] = field(default_factory=list)


class BarAggregator:
    def __init__(self, timeframe: int) -> None:
        if timeframe < 1:
            raise ValueError("timeframe must be >= 1")
        self.tf = timeframe
        self._open: dict[str, _Window] = {}

    def _window_start(self, bar: Bar) -> int:
        return (bar.minute_of_session // self.tf) * self.tf

    def _build(self, w: _Window) -> Bar:
        first, last = w.bars[0], w.bars[-1]
        start_ts = first.ts - timedelta(minutes=first.minute_of_session - w.start_minute)
        # window end relative to the session close (handles early closes / partial final windows)
        end_minute = w.start_minute + self.tf
        minutes_to_close = last.minutes_to_close - (end_minute - (last.minute_of_session + 1))
        return Bar(
            symbol=first.symbol,
            ts=start_ts,
            open=first.open,
            high=max(b.high for b in w.bars),
            low=min(b.low for b in w.bars),
            close=last.close,
            volume=sum(b.volume for b in w.bars),
            timeframe=self.tf,
            session=first.session,
            minute_of_session=w.start_minute,
            minutes_to_close=max(minutes_to_close, 0),
            is_last=last.is_last or minutes_to_close <= 0,
        )

    def flush_before(self, bar: Bar) -> Bar | None:
        """Call BEFORE processing ``bar``: emits the previous window for its symbol if ``bar`` proves it
        complete (new window or new session)."""
        w = self._open.get(bar.symbol)
        if w is None:
            return None
        last = w.bars[-1]
        if last.session != bar.session or self._window_start(bar) != w.start_minute:
            del self._open[bar.symbol]
            return self._build(w)
        return None

    def add(self, bar: Bar) -> Bar | None:
        """Add a 1m bar; returns the completed N-minute bar if this minute closes the window."""
        if self.tf == 1:
            return bar
        ws = self._window_start(bar)
        w = self._open.get(bar.symbol)
        if w is None or w.start_minute != ws or w.bars[-1].session != bar.session:
            w = _Window(ws)
            self._open[bar.symbol] = w
        w.bars.append(bar)
        if bar.minute_of_session + 1 >= ws + self.tf or bar.is_last:
            del self._open[bar.symbol]
            return self._build(w)
        return None
