"""Control strategies: inverse signals, random entries, buy-and-hold."""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from ttbot.data.models import Bar
from ttbot.strategies.base import BaseState, Signal, Strategy


class InverseStrategy(Strategy[None]):
    """Takes the exact opposite side of every signal from a wrapped strategy, at the same time.

    Exits are mirrored too (``inverse=True``): the inverse stop sits at the original take-profit
    distance and the inverse target at the original stop distance. Before costs, each inverse trade's
    return on notional is therefore exactly the negative of the original's - if a pattern has no edge,
    BOTH lose roughly the trading costs."""

    kind = "inverse"
    defaults: ClassVar[dict[str, Any]] = {}

    def __init__(self, inner: Strategy[Any]) -> None:
        super().__init__(f"inv_{inner.name}", inner.timeframe, {}, inner.symbols, inner.atr_period)
        self.inner = inner

    def new_state(self) -> None:
        return None

    def evaluate(self, base: BaseState, st: None, bar: Bar, new_session: bool) -> Signal | None:
        raise NotImplementedError  # pragma: no cover - on_bar is overridden

    def on_bar(self, bar: Bar) -> Signal | None:
        sig = self.inner.on_bar(bar)
        if sig is None:
            return None
        return replace(sig, strategy=self.name, side=-sig.side, inverse=True)


@dataclass
class _RandState:
    rng: random.Random


class RandomStrategy(Strategy[_RandState]):
    """Enters with probability ``trades_per_day / bars_per_session`` on every bar, coin-flip direction,
    ATR stop and the same exits as every other strategy. Seeded per (seed, name, symbol) -> reproducible."""

    kind = "random"
    defaults = {"trades_per_day": 2.0, "seed": 42, "session_minutes": 390}

    def new_state(self) -> _RandState:
        raise NotImplementedError  # created lazily per symbol below

    def on_bar(self, bar: Bar) -> Signal | None:
        if bar.symbol not in self._states:
            seed = zlib.crc32(f"{self.params['seed']}:{self.name}:{bar.symbol}".encode())
            from ttbot.indicators import WilderATR

            self._states[bar.symbol] = (BaseState(atr=WilderATR(self.atr_period)), _RandState(random.Random(seed)))
        return super().on_bar(bar)

    def evaluate(self, base: BaseState, st: _RandState, bar: Bar, new_session: bool) -> Signal | None:
        p = self.params
        prob = p["trades_per_day"] / (p["session_minutes"] / self.timeframe)
        u, side_u = st.rng.random(), st.rng.random()  # always draw twice -> stable stream
        if not base.atr.value or u >= prob:
            return None
        return self.signal(base, bar, 1 if side_u < 0.5 else -1)


@dataclass
class _HoldState:
    bought: bool = False


class BuyAndHoldStrategy(Strategy[_HoldState]):
    """Buys ``symbol`` on the first bar and never sells (no stop, no target, no EOD flatten)."""

    kind = "buy_hold"
    defaults: ClassVar[dict[str, Any]] = {}

    def new_state(self) -> _HoldState:
        return _HoldState()

    def evaluate(self, base: BaseState, st: _HoldState, bar: Bar, new_session: bool) -> Signal | None:
        if st.bought:
            return None
        st.bought = True
        return replace(self.signal(base, bar, 1), hold=True)
