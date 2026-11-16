"""Strategy interface. Strategies are pure signal generators: they see one completed bar at a time and
may emit a Signal that is executed at the NEXT bar's open by the portfolio (never on the signal bar)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Generic, TypeVar

from ttbot.data.models import Bar
from ttbot.indicators import WilderATR


@dataclass(frozen=True, slots=True)
class Signal:
    strategy: str
    symbol: str
    side: int  # +1 long / -1 short
    ts: datetime  # when the signal became known (signal bar end)
    ref_price: float  # close of the signal bar
    atr: float | None
    stop: float | None = None  # structural invalidation level; None -> ATR stop
    hold: bool = False  # buy-and-hold: no stop/target/time/EOD exits
    inverse: bool = False  # mirror exits (stop <-> target) of an inverted signal
    meta: dict[str, Any] = field(default_factory=dict)
    session: date | None = None  # trading session of the signal bar


@dataclass
class BaseState:
    index: int = -1
    session: Any = None
    atr: WilderATR = field(default_factory=WilderATR)
    history: deque[Bar] = field(default_factory=lambda: deque(maxlen=400))

    @property
    def first_index(self) -> int:
        return self.index - len(self.history) + 1


S = TypeVar("S")


class Strategy(ABC, Generic[S]):
    kind: ClassVar[str] = "base"
    defaults: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        name: str,
        timeframe: int,
        params: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        atr_period: int = 14,
    ) -> None:
        self.name = name
        self.timeframe = timeframe
        self.params: dict[str, Any] = {**self.defaults, **(params or {})}
        unknown = set(self.params) - set(self.defaults)
        if unknown:
            raise ValueError(f"{name}: unknown params {sorted(unknown)}")
        self.symbols = symbols
        self.atr_period = atr_period
        self._states: dict[str, tuple[BaseState, S]] = {}

    # ---------------------------------------------------------------- hooks
    @abstractmethod
    def new_state(self) -> S: ...

    @abstractmethod
    def evaluate(self, base: BaseState, st: S, bar: Bar, new_session: bool) -> Signal | None: ...

    # ------------------------------------------------------------- plumbing
    def on_bar(self, bar: Bar) -> Signal | None:
        if bar.timeframe != self.timeframe:
            raise ValueError(f"{self.name} expects {self.timeframe}m bars, got {bar.timeframe}m")
        if self.symbols is not None and bar.symbol not in self.symbols:
            return None
        if bar.symbol not in self._states:
            self._states[bar.symbol] = (BaseState(atr=WilderATR(self.atr_period)), self.new_state())
        base, st = self._states[bar.symbol]
        base.index += 1
        new_session = base.session != bar.session
        base.session = bar.session
        base.history.append(bar)
        base.atr.update(bar.high, bar.low, bar.close)
        return self.evaluate(base, st, bar, new_session)

    def signal(self, base: BaseState, bar: Bar, side: int, stop: float | None = None, **meta: Any) -> Signal:
        return Signal(
            strategy=self.name,
            symbol=bar.symbol,
            side=side,
            ts=bar.end,
            ref_price=bar.close,
            atr=base.atr.value,
            stop=stop,
            meta=meta,
            session=bar.session,
        )
