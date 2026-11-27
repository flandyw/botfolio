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
FAST = timedelta(minutes=5)  # bar size of the VWAP reversion and opening-range strategies
FINAL = {"submitted", "duplicate event", "broker rejected entry; inspect journal"}  # never retried the same day
# Answers about one symbol; any other refusal (paused, halted, full, closed) applies to every signal.
OVERNIGHT = {"etf_trend", "etf_rotation", "pullback"}  # strategies whose positions the parked cash makes room for
PER_SYMBOL = FINAL | {"symbol occupied", "stale/invalid/illiquid quote", "size below one share or invalid stop"}


def _regular(bars: pd.DataFrame, calendar, slot: timedelta = SLOT) -> pd.DataFrame:
    """Regular-session bars with their New York day and slot (1 = the first bar of the session)."""
    if bars.empty:
        return bars.assign(day=None, slot=None)
    df = bars.reset_index(drop=True)
    df["day"] = df["ts"].dt.tz_convert("America/New_York").dt.date
    sessions = {day: calendar.session(day) for day in df["day"].unique()}
    opened = pd.to_datetime(df["day"].map(lambda d: sessions[d].open if sessions[d] else pd.NaT), utc=True)
    closed = pd.to_datetime(df["day"].map(lambda d: sessions[d].close if sessions[d] else pd.NaT), utc=True)
    keep = (df["ts"] >= opened) & (df["ts"] < closed)
    df = df[keep].sort_values(["symbol", "ts"])
    df["slot"] = ((df["ts"] - opened[keep]) // slot + 1).astype(int)
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
                           close=float(close), levels={"Noise high": float(upper), "Noise low": float(lower),
                                                       "Session VWAP": float(vwap)},
                           long_intact=bool(close >= max(upper, vwap)), short_intact=bool(close <= min(lower, vwap)))
    return out


def vwap_states(today: pd.DataFrame, now: datetime, calendar, band_sd: float) -> dict[str, dict]:
    """VWAP mean reversion at the last completed 5-minute bar.

    The bands are session VWAP +/- ``band_sd`` volume-weighted standard deviations of the typical price.
    Long when the close is below the lower band, short above the upper; the stop is one more deviation
    beyond the close. A position has reverted once the close is back at VWAP."""
    df = _regular(today, calendar, FAST)
    out = {}
    if df.empty:
        return out
    df = df[df["ts"] + FAST <= now]
    for symbol, bars in df.groupby("symbol"):
        volume = bars["volume"].sum()
        if len(bars) < 6 or not volume > 0:  # the first half hour has too few bars for a stable deviation
            continue
        typical = (bars["high"] + bars["low"] + bars["close"]) / 3
        vwap = (typical * bars["volume"]).sum() / volume
        sd = (((typical - vwap) ** 2 * bars["volume"]).sum() / volume) ** .5
        close = bars.iloc[-1]["close"]
        if not sd > 0:
            continue
        z = (close - vwap) / sd
        side = 1 if z < -band_sd else -1 if z > band_sd else 0
        out[symbol] = dict(side=side, stop=float(close - side * sd), strength=float(abs(z)), vwap=float(vwap),
                           bar_end=(bars.iloc[-1]["ts"] + FAST).to_pydatetime(),
                           close=float(close), levels={"Upper band": float(vwap + band_sd * sd),
                                                       "Lower band": float(vwap - band_sd * sd),
                                                       "Session VWAP": float(vwap)},
                           long_reverted=bool(close >= vwap), short_reverted=bool(close <= vwap))
    return out


def opening_volume(history: pd.DataFrame, lookback: int, calendar) -> pd.Series:
    """Mean volume of the first 15 minutes over the last ``lookback`` sessions, from 15-minute bars before today."""
    df = _regular(history, calendar, timedelta(minutes=15))
    if df.empty:
        return pd.Series(dtype=float)
    first = df[df["slot"] == 1].groupby("symbol").tail(lookback).groupby("symbol")["volume"]
    return first.mean()[first.count() >= lookback]


def orb_states(avg_volume: pd.Series, today: pd.DataFrame, now: datetime, calendar) -> dict[str, dict]:
    """15-minute opening-range breakout (after Zarattini, Barbon & Aziz 2024) at the last completed 5-minute bar.

    A symbol trades only in the direction of its opening-range candle: long once a close clears the range
    high after a rising first 15 minutes, short below the low after a falling one. The stop is the other
    side of the range. Relative volume (opening volume over its recent average) ranks the candidates."""
    df = _regular(today, calendar, FAST)
    out = {}
    if df.empty or avg_volume.empty:
        return out
    df = df[df["ts"] + FAST <= now]
    for symbol, bars in df.groupby("symbol"):
        opening = bars[bars["slot"] <= 3]
        if len(opening) < 3 or len(bars) < 4 or not avg_volume.get(symbol, 0) > 0:
            continue
        high, low = opening["high"].max(), opening["low"].min()
        first, last = opening.iloc[0]["open"], opening.iloc[-1]["close"]
        direction = 1 if last > first else -1 if last < first else 0
        close = bars.iloc[-1]["close"]
        broke = close > high if direction == 1 else close < low if direction == -1 else False
        out[symbol] = dict(side=direction if broke else 0, stop=float(low if direction == 1 else high),
                           rvol=float(opening["volume"].sum() / avg_volume[symbol]),
                           close=float(close), levels={"Opening high": float(high), "Opening low": float(low)},
                           bar_end=(bars.iloc[-1]["ts"] + FAST).to_pydatetime())
    return out


def pullback_states(daily: pd.DataFrame, sma_days: int, entry_rsi: float, exit_sma: int) -> dict[str, dict]:
    """Connors RSI(2) pullback on completed daily bars: "buy" a stock above its long average whose 2-day RSI
    is below ``entry_rsi``, "sell" once it closes above its ``exit_sma``-day average, else None."""
    out = {}
    for symbol, bars in daily.sort_values("ts").groupby("symbol"):
        closes = bars["close"].reset_index(drop=True)
        if len(closes) < max(sma_days, exit_sma, 3):
            continue
        change = closes.diff()
        gain = change.clip(lower=0).ewm(alpha=.5, adjust=False).mean().iloc[-1]
        loss = (-change.clip(upper=0)).ewm(alpha=.5, adjust=False).mean().iloc[-1]
        rsi = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        close = closes.iloc[-1]
        state = ("sell" if close > closes.tail(exit_sma).mean()
                 else "buy" if close > closes.tail(sma_days).mean() and rsi < entry_rsi else None)
        out[symbol] = dict(state=state, rsi=float(rsi), levels={
            f"SMA {sma_days}": float(closes.tail(sma_days).mean()),
            f"SMA {exit_sma}": float(closes.tail(exit_sma).mean()),
        })
    return out


def trend_states(daily: pd.DataFrame, sma_days: int, band_pct: float, *, context=None) -> dict[str, str | None]:
    """"long" above the moving average, "flat" below it, None inside the band (keep the current state)."""
    out = {}
    for symbol, bars in daily.sort_values("ts").groupby("symbol"):
        closes = bars["close"].tail(sma_days)
        if len(closes) < sma_days:
            continue
        sma, close = closes.mean(), closes.iloc[-1]
        out[symbol] = ("long" if close > sma * (1 + band_pct / 100)
                       else "flat" if close < sma * (1 - band_pct / 100) else None)
        if context is not None and out[symbol] == "long":
            context[symbol] = {"levels": {f"SMA {sma_days}": float(sma),
                                          "Entry threshold": float(sma * (1 + band_pct / 100)),
                                          "Exit threshold": float(sma * (1 - band_pct / 100))}}
    return out


def rotation_members(daily: pd.DataFrame, held: set[str], top: int, keep: int, sma_days: int, *,
                     context=None) -> list[str]:
    """The ETFs to hold: the `top` by momentum, keeping a holding until it leaves the top `keep`.

    Momentum is the average of the 1, 3, 6 and 12-month returns. An ETF qualifies only with positive
    momentum and a close above its moving average, so fewer than `top` are held in a weak market.
    """
    scores, eligible = {}, {}
    for symbol, bars in daily.sort_values("ts").groupby("symbol"):
        closes = bars["close"].to_numpy()
        if len(closes) < max(253, sma_days):
            continue
        scores[symbol] = sum(closes[-1] / closes[-1 - days] - 1 for days in (21, 63, 126, 252)) / 4
        sma = closes[-sma_days:].mean()
        if scores[symbol] > 0 and closes[-1] > sma:
            eligible[symbol] = float(sma)
    ranked = sorted(scores, key=scores.get, reverse=True)
    members = [symbol for symbol in ranked[:keep] if symbol in held and symbol in eligible]
    for symbol in ranked[:top]:
        if len(members) < top and symbol in eligible and symbol not in members:
            members.append(symbol)
    if context is not None:
        for rank, symbol in enumerate(ranked, 1):
            if symbol in members:
                context[symbol] = {"levels": {f"SMA {sma_days}": eligible[symbol]},
                                   "metrics": {"Rank": f"{rank} of {len(ranked)}",
                                               "Momentum": f"{scores[symbol]:+.2%}"}}
    return members


def intraday_context(state, **metrics):
    return dict(at=state["bar_end"].isoformat(), price=state["close"], levels=state["levels"], metrics=metrics)


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
            self._daily, self._rotation, self._fast, self._orb_volume, self._pullback = {}, None, None, None, None
        # Rotation runs first so the trend strategy knows which ETFs it has handed over; parked cash runs last.
        for name, enabled, step in (("etf_rotation", strategies.etf_rotation.enabled, self._etf_rotation),
                                    ("etf_trend", strategies.etf_trend.enabled, self._etf_trend),
                                    ("momentum", strategies.momentum.enabled, self._momentum),
                                    ("orb", strategies.orb.enabled, self._orb),
                                    ("vwap_reversion", strategies.vwap_reversion.enabled, self._vwap_reversion),
                                    ("pullback", strategies.pullback.enabled, self._pullback_step),
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
        if cfg.symbols:
            symbols = [symbol for symbol in symbols if symbol in set(cfg.symbols)]
        held = self.paper.open_intents("momentum")
        for intent in held:  # no bars are read for these, so the noise-band exit below would never see them
            if intent["symbol"] not in symbols:
                self.paper.request_exit("momentum", intent["symbol"], "removed from the momentum symbols")
        if not symbols:
            return "no momentum symbols on the watchlist"
        slot = int((now - session.open - timedelta(seconds=20)) // SLOT)  # bars publish shortly after they end
        if slot < 1:
            return "waiting for the first 30-minute bar"
        if self._slot is None or self._slot[:2] != (slot, symbols):
            self._slot = (slot, symbols, self._momentum_states(now, session, symbols), 0)
        states, submitted = self._slot[2:]
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
        signals = [Signal("momentum", f"momentum-{symbol}-{self._day}", symbol, state["side"], stop=state["stop"],
                          context=intraday_context(state))
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

    def _daily_bars(self, symbols):
        """Completed daily bars, fetched once a day per symbol list and shared by the daily strategies."""
        key = tuple(symbols)
        if key not in self._daily:
            strategies = self.cfg.strategies
            days = max(strategies.etf_trend.sma_days, strategies.pullback.sma_days)
            if strategies.etf_rotation.enabled:
                days = max(days, strategies.etf_rotation.sma_days, 253)
            end = pd.Timestamp(self._day, tz="America/New_York")
            daily = self.client.get_bars(symbols, (end - timedelta(days=int(days * 1.5) + 30)).to_pydatetime(),
                                         end.to_pydatetime(), "1Day", self.cfg.data.feed, "all")
            self._daily[key] = daily[daily["ts"] < end]
        return self._daily[key]

    def _stocks(self, symbols, chosen):
        """The configured symbols on the watchlist; when none are configured, every watchlist symbol that the
        ETF strategies and intraday momentum do not trade."""
        if chosen:
            return [symbol for symbol in symbols if symbol in set(chosen)]
        reserved = set(self.cfg.etfs)
        if self.cfg.strategies.momentum.enabled:
            reserved |= set(self.cfg.strategies.momentum.symbols)
        return [symbol for symbol in symbols if symbol not in reserved]

    def _fast_bars(self, now, session, symbols):
        """Today's 5-minute bars, fetched once per bar for every symbol the fast strategies ask for."""
        slot = int((now - session.open - timedelta(seconds=20)) // FAST)  # bars publish shortly after they end
        if self._fast is None or self._fast[0] != slot or not set(symbols) <= self._fast[1]:
            wanted = set(symbols) | (self._fast[1] if self._fast and self._fast[0] == slot else set())
            self._fast = (slot, wanted, self.client.get_bars(sorted(wanted), session.open, now, "5Min",
                                                             self.cfg.data.feed, "split"))
        bars = self._fast[2]
        return bars[bars["symbol"].isin(symbols)]

    def _enter(self, name, candidates, held, signal):
        """Send the strongest fresh candidates (already sorted) to fill the strategy's free positions."""
        free = getattr(self.cfg.strategies, name).max_positions - len(held)
        taken = {intent["symbol"] for intent in self.paper.open_intents()}
        signals = [signal(symbol, state) for symbol, state in candidates
                   if symbol not in taken and f"{name}-{symbol}-{self._day}" not in self._done]
        results = self._send(name, signals[:max(0, free) * 3], free)
        return sum(result == "submitted" for result in results.values())

    def _vwap_reversion(self, now, session, symbols):
        cfg = self.cfg.strategies.vwap_reversion
        symbols = self._stocks(symbols, cfg.symbols)
        held = self.paper.open_intents("vwap_reversion")
        for intent in held:  # no bars are read for these, so the VWAP exit below would never see them
            if intent["symbol"] not in symbols:
                self.paper.request_exit("vwap_reversion", intent["symbol"], "removed from the VWAP symbols")
        if not symbols:
            return "no VWAP reversion symbols on the watchlist"
        if now - session.open < 6 * FAST + timedelta(seconds=20):
            return "waiting for the first half hour"
        states = vwap_states(self._fast_bars(now, session, symbols), now, self.calendar, cfg.band_sd)
        for intent in held:
            state = states.get(intent["symbol"])
            if state and state["long_reverted" if intent["side"] == "long" else "short_reverted"]:
                self.paper.request_exit("vwap_reversion", intent["symbol"], "price back at VWAP")
        stretched = sorted(((symbol, state) for symbol, state in states.items()
                            if state["side"] and now - state["bar_end"] <= timedelta(minutes=10)),
                           key=lambda item: item[1]["strength"], reverse=True)
        note = f"{len(stretched)} beyond {cfg.band_sd:g} deviations of VWAP in {len(states)} symbols; {len(held)} held"
        if session.close - now < timedelta(minutes=cfg.hold_minutes):
            return note + "; no entries this close to the close"
        submitted = self._enter("vwap_reversion", stretched, held, lambda symbol, state: Signal(
            "vwap_reversion", f"vwap_reversion-{symbol}-{self._day}", symbol, state["side"], stop=state["stop"],
            hold_minutes=cfg.hold_minutes, context=intraday_context(state, **{"VWAP distance": f'{state["strength"]:.2f} SD'})))
        return note + f", {submitted} orders submitted"

    def _orb(self, now, session, symbols):
        cfg = self.cfg.strategies.orb
        symbols = self._stocks(symbols, cfg.symbols)
        if not symbols:
            return "no opening-range symbols on the watchlist"
        if now - session.open < 4 * FAST + timedelta(seconds=20):
            return "waiting for the opening range and the bar after it"
        if now - session.open > timedelta(minutes=cfg.entry_minutes):
            return f"entries closed {cfg.entry_minutes} minutes after the open; open positions close by the close"
        if self._orb_volume is None or self._orb_volume[0] != symbols:
            history = self.client.get_bars(symbols, session.open - timedelta(days=int(cfg.lookback * 1.5) + 7),
                                           session.open, "15Min", self.cfg.data.feed, "split")
            self._orb_volume = (symbols, opening_volume(history, cfg.lookback, self.calendar))
        states = orb_states(self._orb_volume[1], self._fast_bars(now, session, symbols), now, self.calendar)
        # Zarattini et al. trade only the names with the most unusual opening volume ("stocks in play").
        in_play = sorted(((symbol, state) for symbol, state in states.items() if state["rvol"] >= cfg.min_rvol),
                         key=lambda item: item[1]["rvol"], reverse=True)[:cfg.top]
        breakouts = [(symbol, state) for symbol, state in in_play
                     if state["side"] and now - state["bar_end"] <= timedelta(minutes=10)]
        held = self.paper.open_intents("orb")
        submitted = self._enter("orb", breakouts, held, lambda symbol, state: Signal(
            "orb", f"orb-{symbol}-{self._day}", symbol, state["side"], stop=state["stop"],
            context=intraday_context(state, **{"Relative volume": f'{state["rvol"]:.2f}×'})))
        return (f"{len(in_play)} in play (relative volume >= {cfg.min_rvol:g}), {len(breakouts)} breaking out; "
                f"{len(held)} held, {submitted} orders submitted")

    def _pullback_step(self, now, session, symbols):
        cfg = self.cfg.strategies.pullback
        symbols = self._stocks(symbols, cfg.symbols)
        if now - session.open < timedelta(minutes=5):
            return "waiting for opening quotes to settle"
        if self._pullback is None or self._pullback[0] != symbols:
            states = pullback_states(self._daily_bars(symbols), cfg.sma_days, cfg.entry_rsi, cfg.exit_sma) if symbols else {}
            self._pullback = (symbols, states)
        states = self._pullback[1]
        held = self.paper.open_intents("pullback")
        for intent in held:
            if intent["symbol"] not in symbols:
                self.paper.request_exit("pullback", intent["symbol"], "removed from the pullback symbols")
            elif states.get(intent["symbol"], {}).get("state") == "sell":
                self.paper.request_exit("pullback", intent["symbol"], f"closed above its {cfg.exit_sma}-day average")
        mine = {intent["symbol"] for intent in held}
        oversold = sorted(((symbol, state) for symbol, state in states.items()
                           if state["state"] == "buy" and symbol not in mine), key=lambda item: item[1]["rsi"])
        submitted = self._enter("pullback", oversold, held, lambda symbol, state: Signal(
            "pullback", f"pullback-{symbol}-{self._day}", symbol, 1, intraday=False, fractional=True,
            context=dict(levels=state["levels"], metrics={"RSI(2)": round(state["rsi"], 2), "Entry RSI": cfg.entry_rsi})))
        return (f"{len(oversold)} of {len(states)} stocks oversold (RSI(2) < {cfg.entry_rsi:g}) above their "
                f"{cfg.sma_days}-day average; {len(held)} held, {submitted} orders submitted")

    def _etf_rotation(self, now, session, symbols):
        cfg = self.cfg.strategies.etf_rotation
        etfs = [symbol for symbol in symbols if symbol in set(self.cfg.etfs)]
        if now - session.open < timedelta(minutes=5):
            return "waiting for opening quotes to settle"
        held = {intent["symbol"] for intent in self.paper.open_intents("etf_rotation")}
        if self._rotation is None or self._rotation[0] != etfs:
            context = {}
            members = rotation_members(self._daily_bars(etfs), held, cfg.top, cfg.keep, cfg.sma_days,
                                       context=context) if etfs else []
            self._rotation = (etfs, members, context)
        _, members, context = self._rotation
        for symbol in held.difference(members):
            self.paper.request_exit("etf_rotation", symbol, "fell out of the momentum ranking")
        wanted = [symbol for symbol in members if symbol not in held]
        for symbol in wanted:  # the trend strategy sells its smaller position first; the buy follows once it is flat
            self.paper.request_exit("etf_trend", symbol, "handed to the rotation strategy")
        taken = {intent["symbol"] for intent in self.paper.open_intents()}
        signals = [Signal("etf_rotation", f"etf_rotation-{symbol}-{self._day}", symbol, 1, intraday=False,
                          fractional=True, context=context[symbol])
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
            context = {}
            states = trend_states(self._daily_bars(etfs), cfg.sma_days, cfg.band_pct, context=context) if etfs else {}
            self._trend = (etfs, states, context)
        _, states, context = self._trend
        rotation = set(self._rotation[1]) if self.cfg.strategies.etf_rotation.enabled and self._rotation else set()
        held = {intent["symbol"] for intent in self.paper.open_intents("etf_trend")}
        for symbol in held:
            if symbol not in etfs:
                self.paper.request_exit("etf_trend", symbol, "removed from the ETF watchlist")
            elif states.get(symbol) == "flat":
                self.paper.request_exit("etf_trend", symbol, f"closed below the {cfg.sma_days}-day average")
        signals = [Signal("etf_trend", f"etf_trend-{symbol}-{self._day}", symbol, 1, intraday=False, fractional=True,
                          context=context[symbol])
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
                                                      fractional=True, position_pct=target,
                                                      context={"metrics": {"Target allocation": f"{target:.1f}%"}})])
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
