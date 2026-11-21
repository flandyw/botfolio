"""Rule-based strategies that trade the watchlist on Alpaca Paper alongside the news strategy.

Each strategy only decides what to hold. Every entry and exit is a real order placed by the shared
paper executor, which also keeps one strategy per symbol, sizes positions and reconciles the broker.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pandas as pd

from botfolio.config import watchlist
from botfolio.data.calendar import get_calendar, ny_date
from botfolio.events.paper import Signal, configured

log = logging.getLogger(__name__)
SLOT = timedelta(minutes=30)
FINAL = {"submitted", "duplicate event", "broker rejected entry; inspect journal"}  # never retried the same day
# Answers about one symbol; any other refusal (paused, halted, full, closed) applies to every signal.
OVERNIGHT = {"etf_trend", "etf_rotation"}  # strategies whose positions the parked cash makes room for
PER_SYMBOL = FINAL | {"symbol occupied", "stale/invalid/illiquid quote", "size below one share or invalid stop"}


def _regular(bars: pd.DataFrame, calendar) -> pd.DataFrame:
    """Regular-session bars with their New York day and slot (1 = the first 30 minutes)."""
    if bars.empty:
        return bars.assign(day=None, slot=None)
    df = bars.reset_index(drop=True)
    df["day"] = df["ts"].dt.tz_convert("America/New_York").dt.date
    sessions = {day: calendar.session(day) for day in df["day"].unique()}
    opened = pd.to_datetime(df["day"].map(lambda d: sessions[d].open if sessions[d] else pd.NaT), utc=True)
    closed = pd.to_datetime(df["day"].map(lambda d: sessions[d].close if sessions[d] else pd.NaT), utc=True)
    keep = (df["ts"] >= opened) & (df["ts"] < closed)
    df = df[keep].sort_values(["symbol", "ts"])
    df["slot"] = ((df["ts"] - opened[keep]) // SLOT + 1).astype(int)
    return df


def noise_profile(history: pd.DataFrame, lookback: int, calendar) -> tuple[pd.DataFrame, pd.Series]:
    """Mean absolute move from the open at each time of day over the last ``lookback`` sessions, per
    symbol, and each symbol's previous close. ``history`` holds 30-minute bars before today."""
    df = _regular(history, calendar)
    if df.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    opens = df.groupby(["symbol", "day"])["open"].first()
    closes = df.pivot_table(index=["symbol", "day"], columns="slot", values="close", aggfunc="last").ffill(axis=1)
    recent = closes.div(opens, axis=0).sub(1).abs().groupby(level="symbol").tail(lookback)
    by_symbol = recent.groupby(level="symbol")
    sigma = by_symbol.mean().where(by_symbol.count() >= lookback)
    return sigma, closes.iloc[:, -1].groupby(level="symbol").last()


def momentum_states(profile, today: pd.DataFrame, now: datetime, calendar) -> dict[str, dict]:
    """Noise-band momentum (Zarattini, Aziz & Barbon 2024) at the last completed 30-minute bar.

    The noise area is ``[min(open, prev_close) * (1 - sigma), max(open, prev_close) * (1 + sigma)]``.
    Long when the close is above the area and above session VWAP; short mirrors. The far side of the
    area is the stop. The trend is intact while the close stays beyond max(upper, VWAP)."""
    sigma, prev_close = profile
    df = _regular(today, calendar)
    out = {}
    if df.empty or sigma.empty:
        return out
    df = df[df["ts"] + SLOT <= now]
    for symbol, bars in df.groupby("symbol"):
        last = bars.iloc[-1]
        volume = bars["volume"].sum()
        s = sigma.at[symbol, last["slot"]] if symbol in sigma.index and last["slot"] in sigma.columns else None
        if s is None or not s > 0 or not volume > 0:
            continue
        opened, close = bars.iloc[0]["open"], last["close"]
        vwap = ((bars["high"] + bars["low"] + bars["close"]) / 3 * bars["volume"]).sum() / volume
        upper = max(opened, prev_close[symbol]) * (1 + s)
        lower = min(opened, prev_close[symbol]) * (1 - s)
        side = 1 if close > max(upper, vwap) else -1 if close < min(lower, vwap) else 0
        strength = (close - upper) / (upper * s) if side == 1 else (lower - close) / (lower * s) if side else 0.0
        out[symbol] = dict(side=side, stop=float(lower if side == 1 else upper), strength=float(strength),
                           bar_end=(last["ts"] + SLOT).to_pydatetime(),
                           long_intact=bool(close >= max(upper, vwap)), short_intact=bool(close <= min(lower, vwap)))
    return out


def trend_states(daily: pd.DataFrame, sma_days: int, band_pct: float) -> dict[str, str | None]:
    """"long" above the moving average, "flat" below it, None inside the band (keep the current state)."""
    out = {}
    for symbol, bars in daily.sort_values("ts").groupby("symbol"):
        closes = bars["close"].tail(sma_days)
        if len(closes) < sma_days:
            continue
        sma, close = closes.mean(), closes.iloc[-1]
        out[symbol] = ("long" if close > sma * (1 + band_pct / 100)
                       else "flat" if close < sma * (1 - band_pct / 100) else None)
    return out


def rotation_members(daily: pd.DataFrame, held: set[str], top: int, keep: int, sma_days: int) -> list[str]:
    """The ETFs to hold: the `top` by momentum, keeping a holding until it leaves the top `keep`.

    Momentum is the average of the 1, 3, 6 and 12-month returns. An ETF qualifies only with positive
    momentum and a close above its moving average, so fewer than `top` are held in a weak market.
    """
    scores, eligible = {}, set()
    for symbol, bars in daily.sort_values("ts").groupby("symbol"):
        closes = bars["close"].to_numpy()
        if len(closes) < max(253, sma_days):
            continue
        scores[symbol] = sum(closes[-1] / closes[-1 - days] - 1 for days in (21, 63, 126, 252)) / 4
        if scores[symbol] > 0 and closes[-1] > closes[-sma_days:].mean():
            eligible.add(symbol)
    ranked = sorted(scores, key=scores.get, reverse=True)
    members = [symbol for symbol in ranked[:keep] if symbol in held and symbol in eligible]
    for symbol in ranked[:top]:
        if len(members) < top and symbol in eligible and symbol not in members:
            members.append(symbol)
    return members


class StrategyRunner:
    def __init__(self, cfg, client, paper, storage):
        self.cfg, self.client, self.paper, self.storage = cfg, client, paper, storage
        self.calendar = get_calendar("NASDAQ")
        self.clock = lambda: datetime.now(UTC)
        self.status: dict[str, dict] = {}
        self._day = None

    def run(self) -> dict[str, dict]:
        now = self.clock()
        session = self.calendar.session_for(now)
        symbols = watchlist(self.storage, self.cfg)
        strategies = self.cfg.strategies
        phase = (self.storage.get("event-execution-state") or {}).get("phase")
        if self._day != ny_date(now):
            self._day, self._done, self._slot, self._profile, self._trend = ny_date(now), set(), None, None, None
            self._daily = self._rotation = None
        # Rotation runs first so the trend strategy knows which ETFs it has handed over; parked cash runs last.
        for name, enabled, step in (("etf_rotation", strategies.etf_rotation.enabled, self._etf_rotation),
                                    ("etf_trend", strategies.etf_trend.enabled, self._etf_trend),
                                    ("momentum", strategies.momentum.enabled, self._momentum),
                                    ("cash_park", strategies.cash_park.enabled, self._cash_park)):
            if not enabled:
                note = "disabled in config"
            elif not session:
                note = "market closed"
            elif not configured() or phase != "ready":
                note = "paper execution not ready"
            else:
                note = step(now, session, symbols)
            previous = self.status.get(name, {})
            self.status[name] = dict(previous, at=now.isoformat(), enabled=enabled, note=note or previous.get("note"))
        self.storage.put("strategy-status", self.status)
        return self.status

    def _send(self, name, signals, free=None):
        """Quote and submit a few signals at a time so every order is priced from a fresh quote."""
        results = {}
        for start in range(0, len(signals), 5):
            submitted = sum(result == "submitted" for result in results.values())
            if free is not None and submitted >= free:
                break
            chunk = signals[start:start + 5]
            quotes = self.client.get_latest_quotes([signal.symbol for signal in chunk], self.cfg.data.feed)
            batch = self.paper.submit(chunk, quotes, self.clock())
            results.update(batch)
            self._done.update(key for key, result in batch.items() if result in FINAL)
            if not PER_SYMBOL.intersection(batch.values()):
                break  # paused, halted or full: the rest would get the same answer
        if results:
            by_key = {signal.key: signal for signal in signals}
            self.status.setdefault(name, {})["orders"] = [
                dict(symbol=by_key[key].symbol, side="long" if by_key[key].direction == 1 else "short", result=result)
                for key, result in results.items()][:25]
            log.info("strategy orders", extra={"strategy": name, "results": results})
        return results

    def _momentum(self, now, session, symbols):
        cfg = self.cfg.strategies.momentum
        slot = int((now - session.open - timedelta(seconds=20)) // SLOT)  # bars publish shortly after they end
        if slot < 1:
            return "waiting for the first 30-minute bar"
        if self._slot is None or self._slot[:2] != (slot, symbols):
            self._slot = (slot, symbols, self._momentum_states(now, session, symbols), 0)
        states, submitted = self._slot[2:]
        held = self.paper.open_intents("momentum")
        for intent in held:
            state = states.get(intent["symbol"])
            if state and not state["long_intact" if intent["side"] == "long" else "short_intact"]:
                self.paper.request_exit("momentum", intent["symbol"], "price back inside the noise band")
        # A breakout stays tradable for ten minutes, so a stale quote is retried on the next cycle.
        breakouts = sorted(((symbol, state) for symbol, state in states.items()
                            if state["side"] and now - state["bar_end"] <= timedelta(minutes=10)),
                           key=lambda item: item[1]["strength"], reverse=True)
        free = cfg.max_positions - len(held)
        # Symbols another strategy holds are skipped, so they cannot crowd out the next-strongest breakouts.
        taken = {intent["symbol"] for intent in self.paper.open_intents()}
        signals = [Signal("momentum", f"momentum-{symbol}-{self._day}", symbol, state["side"], stop=state["stop"])
                   for symbol, state in breakouts
                   if symbol not in taken and f"momentum-{symbol}-{self._day}" not in self._done]
        results = self._send("momentum", signals[:max(0, free) * 3], free)
        submitted += sum(result == "submitted" for result in results.values())
        self._slot = (slot, symbols, states, submitted)
        return f"{len(breakouts)} breakouts in {len(states)} symbols at bar {slot}; {submitted} orders submitted"

    def _momentum_states(self, now, session, symbols):
        cfg = self.cfg.strategies.momentum
        if self._profile is None or self._profile[0] != symbols:
            history = self.client.get_bars(symbols, session.open - timedelta(days=int(cfg.lookback * 1.5) + 7),
                                           session.open, "30Min", self.cfg.data.feed, "split")
            self._profile = (symbols, noise_profile(history, cfg.lookback, self.calendar))
        today = self.client.get_bars(symbols, session.open, now, "30Min", self.cfg.data.feed, "split")
        return momentum_states(self._profile[1], today, now, self.calendar)

    def _daily_bars(self, etfs):
        """Completed daily bars for the ETFs, fetched once a day and shared by the trend and rotation strategies."""
        if self._daily is None or self._daily[0] != etfs:
            strategies = self.cfg.strategies
            days = strategies.etf_trend.sma_days
            if strategies.etf_rotation.enabled:
                days = max(days, strategies.etf_rotation.sma_days, 253)
            end = pd.Timestamp(self._day, tz="America/New_York")
            daily = self.client.get_bars(etfs, (end - timedelta(days=int(days * 1.5) + 30)).to_pydatetime(),
                                         end.to_pydatetime(), "1Day", self.cfg.data.feed, "all")
            self._daily = (etfs, daily[daily["ts"] < end])
        return self._daily[1]

    def _etf_rotation(self, now, session, symbols):
        cfg = self.cfg.strategies.etf_rotation
        etfs = [symbol for symbol in symbols if symbol in set(self.cfg.etfs)]
        if now - session.open < timedelta(minutes=5):
            return "waiting for opening quotes to settle"
        held = {intent["symbol"] for intent in self.paper.open_intents("etf_rotation")}
        if self._rotation is None or self._rotation[0] != etfs:
            members = rotation_members(self._daily_bars(etfs), held, cfg.top, cfg.keep, cfg.sma_days) if etfs else []
            self._rotation = (etfs, members)
        members = self._rotation[1]
        for symbol in held.difference(members):
            self.paper.request_exit("etf_rotation", symbol, "fell out of the momentum ranking")
        wanted = [symbol for symbol in members if symbol not in held]
        for symbol in wanted:  # the trend strategy sells its smaller position first; the buy follows once it is flat
            self.paper.request_exit("etf_trend", symbol, "handed to the rotation strategy")
        taken = {intent["symbol"] for intent in self.paper.open_intents()}
        signals = [Signal("etf_rotation", f"etf_rotation-{symbol}-{self._day}", symbol, 1, intraday=False,
                          fractional=True)
                   for symbol in wanted if symbol not in taken and f"etf_rotation-{symbol}-{self._day}" not in self._done]
        results = self._send("etf_rotation", signals)
        return (f"top {cfg.top} by momentum: {', '.join(members) or 'none qualify'}; {len(held)} held, "
                f"{sum(r == 'submitted' for r in results.values())} orders submitted")

    def _etf_trend(self, now, session, symbols):
        cfg = self.cfg.strategies.etf_trend
        etfs = [symbol for symbol in symbols if symbol in set(self.cfg.etfs)]
        if now - session.open < timedelta(minutes=5):
            return "waiting for opening quotes to settle"
        if self._trend is None or self._trend[0] != etfs:
            self._trend = (etfs, trend_states(self._daily_bars(etfs), cfg.sma_days, cfg.band_pct) if etfs else {})
        states = self._trend[1]
        rotation = set(self._rotation[1]) if self.cfg.strategies.etf_rotation.enabled and self._rotation else set()
        held = {intent["symbol"] for intent in self.paper.open_intents("etf_trend")}
        for symbol in held:
            if symbol not in etfs:
                self.paper.request_exit("etf_trend", symbol, "removed from the ETF watchlist")
            elif states.get(symbol) == "flat":
                self.paper.request_exit("etf_trend", symbol, f"closed below the {cfg.sma_days}-day average")
        signals = [Signal("etf_trend", f"etf_trend-{symbol}-{self._day}", symbol, 1, intraday=False, fractional=True)
                   for symbol in etfs if states.get(symbol) == "long" and symbol not in held | rotation
                   and f"etf_trend-{symbol}-{self._day}" not in self._done]
        results = self._send("etf_trend", signals)
        above = sum(state == "long" for state in states.values())
        return (f"{above} of {len(states)} ETFs above the {cfg.sma_days}-day average; {len(held)} held, "
                f"{sum(r == 'submitted' for r in results.values())} orders submitted")


    def _cash_park(self, now, session, symbols):
        cfg = self.cfg.strategies.cash_park
        if cfg.symbol in symbols:
            return f"{cfg.symbol} is on the watchlist, so it cannot also hold the idle cash"
        if now - session.open < timedelta(minutes=30):
            return "waiting for the other strategies' opening orders"
        equity = float(self.paper.client.get_paper_account()["equity"])
        if equity <= 0:
            return "no equity to park"
        values = {p["symbol"]: abs(float(p["market_value"])) for p in self.paper.request("GET", "positions")}

        def pct(intents):
            return 100 * sum(values.get(i["symbol"], i["qty"] * i["limit"]) for i in intents) / equity

        held = self.paper.open_intents("cash_park")
        parked = pct(held)
        target = max(0.0, 100 - cfg.reserve_pct - pct(i for i in self.paper.open_intents()
                                                      if i.get("strategy") in OVERNIGHT))
        key = f"cash_park-{self._day}"
        note = f"{parked:.0f}% of equity in {cfg.symbol}, target {target:.0f}%"
        if held:
            # The position is resized by selling it and buying the new size, at most once a day. Selling
            # when it is too large is never deferred, so the overnight positions are not bought on margin.
            if parked - target > cfg.band_pct or (target - parked > cfg.band_pct and key not in self._done):
                self.paper.request_exit("cash_park", cfg.symbol, f"resizing to {target:.0f}% of equity")
                note += "; resizing"
        elif target >= cfg.band_pct and key not in self._done:
            results = self._send("cash_park", [Signal("cash_park", key, cfg.symbol, 1, intraday=False,
                                                      fractional=True, position_pct=target)])
            note += f"; {results.get(key, 'no order')}"
        return note


async def strategy_loop(runner: StrategyRunner, once: bool = False):
    """Runs beside the news loop and the execution monitor; neither waits on the other."""
    while True:
        try:
            await asyncio.to_thread(runner.run)
        except Exception:
            log.exception("strategy cycle failed; no further strategy entries this cycle")
        if once:
            return
        await asyncio.sleep(30)
