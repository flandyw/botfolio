from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from botfolio.config import AppConfig, load_config, watchlist
from botfolio.data.alpaca import BAR_COLUMNS, AlpacaAPIError
from botfolio.data.calendar import get_calendar
from botfolio.events.paper import TERMINAL, PaperExecutor, Signal
from botfolio.events.store import EventStore
from botfolio.safety import PAPER_TRADING_URL
from botfolio.storage import Storage
from botfolio.strategies import (
    FAST,
    StrategyRunner,
    momentum_states,
    noise_profile,
    opening_volume,
    orb_states,
    pullback_states,
    rotation_members,
    trend_states,
    vwap_states,
)

CAL = get_calendar("NASDAQ")
DAY1 = CAL.session(datetime(2025, 1, 7).date())
DAY2 = CAL.session(datetime(2025, 1, 8).date())
NOW = DAY1.open + timedelta(minutes=91)  # three 30-minute bars are complete


class Broker:
    """Stands in for the Alpaca client; fails any trading call that is not to the paper endpoint."""

    def __init__(self):
        self.now, self.orders, self.positions, self.posts, self.reject = NOW, {}, {}, [], None

    def get_paper_account(self):
        return dict(account_number="PA1", status="ACTIVE", equity="10000", cash="10000", last_equity="10000",
                    buying_power="40000")

    def _request(self, method, url, **kw):
        assert url.startswith(PAPER_TRADING_URL + "/v2/")
        path = url.removeprefix(PAPER_TRADING_URL + "/v2/")
        if path == "clock":
            return dict(is_open=True, timestamp=self.now.isoformat())
        if path == "positions":
            return list(self.positions.values())
        if method == "POST":
            body = kw["json"]
            self.posts.append(body)
            if self.reject:
                raise AlpacaAPIError(self.reject, "rejected")
            legs = [dict(id=f"{body['client_order_id']}-{leg}", status="held")
                    for leg in ("stop_loss", "take_profit") if leg in body]
            order = dict(body, id=body["client_order_id"], status="new", filled_qty="0", legs=legs)
            if body["type"] == "market":
                order.update(status="filled", filled_qty=body["qty"])
                del self.positions[body["symbol"]]
            self.orders[order["id"]] = order
            return order
        if path == "orders:by_client_order_id":
            if kw["params"]["client_order_id"] not in self.orders:
                raise AlpacaAPIError(404, "not found")
            return self.orders[kw["params"]["client_order_id"]]
        if method == "DELETE":
            for order in self.orders.values():
                for item in [order, *order["legs"]]:
                    if item["id"] == path.removeprefix("orders/"):
                        item["status"] = "canceled"
            return None
        assert path == "orders" and kw["params"]["status"] == "open"
        return [o for o in self.orders.values()
                if o["status"] not in TERMINAL and o["symbol"] in kw["params"]["symbols"].split(",")]

    def fill(self):
        for order in self.orders.values():
            if order["status"] == "new":
                order.update(status="filled", filled_qty=order["qty"])
                for leg in order["legs"]:
                    leg["status"] = "new"
                long = order["side"] == "buy"
                self.positions[order["symbol"]] = dict(symbol=order["symbol"], side="long" if long else "short",
                                                       qty=order["qty"] if long else "-" + order["qty"],
                                                       market_value=str(float(order["qty"])
                                                                        * float(order["limit_price"])))


def quote(ask, at=None):
    return dict(bp=ask - .01, ap=ask, t=(at or NOW).isoformat())


@pytest.fixture
def paper(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path / "KILL"))
    store = EventStore(Storage(":memory:"))
    store.storage.put("event-paper-armed", True)
    store.storage.put("event-execution-state", {"phase": "ready"})
    cfg = AppConfig(symbols=["SPY", "TLT", "UP", "DOWN", "FLAT"], etfs=["SPY", "TLT"])
    executor = PaperExecutor(Broker(), store, limits=cfg.limits())
    executor.cfg = cfg
    return executor


def bars(session, moves, step=timedelta(minutes=30), volume=1000):
    """Bars for one session: {symbol: [close per bar]}; each bar opens at the previous close."""
    rows = []
    for symbol, closes in moves.items():
        opened = closes[0]
        for i, close in enumerate(closes[1:]):
            if session.open + i * step < session.close:
                rows.append((symbol, session.open + i * step, opened, max(opened, close),
                             min(opened, close), close, volume))
                opened = close
    frame = pd.DataFrame(rows, columns=BAR_COLUMNS)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame


def history(symbols=("UP", "DOWN", "FLAT")):
    # Every earlier session sits 0.2% above its open all day, so sigma is 0.2% and the last close is 100.2.
    sessions = CAL.sessions_between(datetime(2024, 12, 10).date(), datetime(2025, 1, 6).date())
    return pd.concat([bars(s, {symbol: [100.0] + [100.2] * 13 for symbol in symbols}) for s in sessions])


TODAY = {"UP": [100.2, 100.6, 101.0, 101.5], "DOWN": [100.2, 99.8, 99.4, 99.0], "FLAT": [100.2, 100.3, 100.2, 100.25]}


def daily(end, last):
    days = pd.date_range(end=pd.Timestamp(end.date(), tz="America/New_York") - timedelta(days=1), periods=210)
    return pd.DataFrame([(symbol, day.tz_convert("UTC"), close, close, close, close if i < 209 else final, 1e6)
                         for symbol, (close, final) in last.items() for i, day in enumerate(days)], columns=BAR_COLUMNS)


def test_default_watchlist_has_over_100_symbols_including_tradable_etfs():
    cfg = load_config()
    assert len(cfg.symbols) > 100 and len(set(cfg.symbols)) == len(cfg.symbols)
    assert len(cfg.etfs) >= 20 and set(cfg.etfs) <= set(cfg.symbols)
    assert all(s.enabled for s in (cfg.strategies.news, cfg.strategies.momentum, cfg.strategies.etf_trend))
    # Momentum's index ETFs are not ETF-strategy symbols, so a held position never blocks a breakout.
    momentum = cfg.strategies.momentum.symbols
    assert momentum == ["SPY", "QQQ"] and set(momentum) <= set(cfg.symbols) and not set(momentum) & set(cfg.etfs)
    storage = Storage(":memory:")
    storage.put("event-watchlist", ["ZZZ", "SPY"])  # a list saved before the expansion cannot shrink it
    assert watchlist(storage, cfg) == [*cfg.symbols, "ZZZ"]
    storage.put("watchlist", ["SPY"])
    assert watchlist(storage, cfg) == ["SPY"]


def test_noise_band_and_trend_signals():
    profile = noise_profile(history(), 14, CAL)
    states = momentum_states(profile, bars(DAY1, TODAY), NOW, CAL)
    assert states["UP"]["side"] == 1 and states["UP"]["stop"] == pytest.approx(100.2 * .998)
    assert states["DOWN"]["side"] == -1 and states["DOWN"]["stop"] == pytest.approx(100.2 * 1.002)
    assert states["FLAT"]["side"] == 0
    assert all(state["bar_end"] == DAY1.open + timedelta(minutes=90) for state in states.values())
    # The bar still forming is ignored, and too little history gives no signal at all.
    assert momentum_states(profile, bars(DAY1, TODAY), NOW - timedelta(minutes=30), CAL)["UP"]["side"] == 1
    assert momentum_states(noise_profile(history(), 30, CAL), bars(DAY1, TODAY), NOW, CAL) == {}
    trends = trend_states(daily(NOW, {"SPY": (100, 110), "TLT": (100, 90), "GLD": (100, 100.5)}), 200, 1)
    assert trends == {"SPY": "long", "TLT": "flat", "GLD": None}
    assert trend_states(daily(NOW, {"SPY": (100, 110)}), 250, 1) == {}


def test_each_strategy_places_its_own_paper_order_and_symbols_are_not_shared(paper):
    broker = paper.client
    signals = [Signal("momentum", "momentum-UP", "UP", 1, stop=100.0),
               Signal("momentum", "momentum-DOWN", "DOWN", -1, stop=100.4),
               Signal("etf_trend", "etf_trend-SPY", "SPY", 1, intraday=False, fractional=True),
               Signal("etf_trend", "etf_trend-UP", "UP", 1, intraday=False, fractional=True),
               Signal("etf_trend", "etf_trend-TLT", "TLT", 1, intraday=False, fractional=True)]
    quotes = {"UP": quote(101.5), "DOWN": quote(99.01), "SPY": quote(600.0), "TLT": quote(90.0, NOW - timedelta(minutes=1))}
    results = paper.submit(signals, quotes, NOW)
    assert results == {"momentum-UP": "submitted", "momentum-DOWN": "submitted", "etf_trend-SPY": "submitted",
                       "etf_trend-UP": "symbol occupied", "etf_trend-TLT": "stale/invalid/illiquid quote"}
    up, down, spy = broker.posts
    assert (up["side"], up["order_class"], up["stop_loss"]) == ("buy", "oto", {"stop_price": "100.0"})
    assert float(up["qty"]) == 9 and "take_profit" not in up  # 10% of equity at the 101.6 limit
    assert (down["side"], down["order_class"], down["stop_loss"]) == ("sell", "oto", {"stop_price": "100.4"})
    assert "order_class" not in spy and 0 < float(spy["qty"]) < 5  # a fractional share of a $600 ETF
    assert float(spy["qty"]) * float(spy["limit_price"]) <= 2500
    assert all(o["type"] == "limit" and o["time_in_force"] == "day" for o in broker.posts)
    intents = {i["symbol"]: i for i in paper.open_intents()}
    assert intents["SPY"]["strategy"] == "etf_trend" and intents["SPY"]["deadline"] is None
    assert datetime.fromisoformat(intents["UP"]["deadline"]) == DAY1.close - timedelta(minutes=5)
    # A second attempt cannot create another order for a symbol that is already working.
    assert paper.submit(signals[:1], quotes, NOW) == {"momentum-UP": "symbol occupied"}
    assert len(broker.posts) == 3


def test_strategy_caps_and_definitive_rejections(paper):
    paper.limits["momentum"]["max_positions"] = 1
    signals = [Signal("momentum", f"momentum-{s}", s, 1, stop=100.0) for s in ("UP", "FLAT")]
    quotes = {"UP": quote(101.5), "FLAT": quote(101.5), "SPY": quote(600.0)}
    assert paper.submit([Signal("momentum", "x", "FLAT", 1, stop=101.59)], quotes, NOW)["x"].endswith("invalid stop")
    assert list(paper.submit(signals, quotes, NOW).values()) == ["submitted", "max concurrent positions"]
    assert paper.submit([Signal("other", "y", "FLAT", 1)], quotes, NOW)["y"] == "unknown strategy or direction"
    paper.client.reject = 422
    trend = Signal("etf_trend", "etf_trend-SPY", "SPY", 1, intraday=False, fractional=True)
    assert paper.submit([trend], quotes, NOW)[trend.key] == "broker rejected entry; inspect journal"
    paper.client.reject = None
    assert [i["symbol"] for i in paper.maintain(NOW)] == ["UP"]  # the rejected reservation is closed, not unknown


def test_a_missing_reservation_does_not_hold_up_other_exits(paper):
    broker = paper.client
    quotes = {"UP": quote(101.5), "FLAT": quote(101.5)}
    paper.submit([Signal("momentum", "momentum-UP", "UP", 1, stop=100.0)], quotes, NOW)
    broker.fill()
    paper.submit([Signal("momentum", "momentum-FLAT", "FLAT", 1, stop=100.0)], quotes, NOW)
    del broker.orders["evt-momentum-FLAT"]  # the reservation never reached the broker
    late = DAY1.close - timedelta(minutes=4)
    for _ in range(2):  # cancel the stop, then close the position
        with pytest.raises(RuntimeError, match="manual reconciliation"):
            paper.maintain(late)
    assert (broker.posts[-1]["symbol"], broker.posts[-1]["type"], broker.posts[-1]["side"]) == ("UP", "market", "sell")
    assert "UP" not in broker.positions


def test_paused_entries_reach_no_strategy(paper):
    paper.store.storage.put("event-paper-armed", False)
    signal = Signal("etf_trend", "etf_trend-SPY", "SPY", 1, intraday=False, fractional=True)
    assert paper.submit([signal], {"SPY": quote(600.0)}, NOW) == {signal.key: "paper entries paused in dashboard"}
    assert not paper.client.posts


def test_pause_during_a_batch_stops_the_remaining_orders(paper):
    broker, request = paper.client, paper.client._request

    def pause_after_first_order(method, url, **kw):
        result = request(method, url, **kw)
        if method == "POST":
            paper.store.storage.put("event-paper-armed", False)
        return result

    broker._request = pause_after_first_order
    signals = [Signal("momentum", f"momentum-{s}", s, 1, stop=100.0) for s in ("UP", "FLAT")]
    assert paper.submit(signals, {"UP": quote(101.5), "FLAT": quote(101.5)}, NOW) == {
        "momentum-UP": "submitted", "momentum-FLAT": "paper entries paused in dashboard"}
    assert len(broker.posts) == 1


def trending(end, gains, periods=260):
    """Daily closes that rise steadily from 100 to 100 * (1 + gain) for each symbol."""
    days = pd.date_range(end=pd.Timestamp(end.date(), tz="America/New_York") - timedelta(days=1), periods=periods)
    rows = []
    for symbol, gain in gains.items():
        for i, day in enumerate(days):
            close = 100 * (1 + gain) ** (i / (periods - 1))
            rows.append((symbol, day.tz_convert("UTC"), close, close, close, close, 1e6))
    return pd.DataFrame(rows, columns=BAR_COLUMNS)


class Data:
    def __init__(self):
        self.daily, self.today, self.asks = {"SPY": (100, 110), "TLT": (100, 90)}, TODAY, {}
        self.quoted_at, self.gains, self.daily_frame = NOW, None, None
        self.fast = self.opening = pd.DataFrame(columns=BAR_COLUMNS)

    def get_bars(self, symbols, start, end, timeframe, feed, adjustment):
        if timeframe == "1Day":
            frame = (self.daily_frame if self.daily_frame is not None
                     else trending(end, self.gains) if self.gains else daily(end, self.daily))
        elif timeframe == "5Min":
            frame = self.fast
        elif timeframe == "15Min":
            frame = self.opening
        elif end <= DAY1.open or DAY1.close < end <= DAY2.open:
            frame = history()
        else:
            frame = bars(DAY1, self.today) if end < DAY1.close else pd.DataFrame(columns=BAR_COLUMNS)
        return frame[frame["symbol"].isin(symbols)]

    def get_latest_quotes(self, symbols, feed):
        return {symbol: quote(self.asks.get(symbol, 100.0), self.quoted_at) for symbol in symbols}


def test_runner_trades_etfs_and_momentum_live_then_exits_through_the_broker(paper):
    broker, data = paper.client, Data()
    data.asks = {"SPY": 600.0, "UP": 101.5, "DOWN": 99.01}
    runner = StrategyRunner(paper.cfg, data, paper, paper.store.storage)
    runner.clock = lambda: broker.now

    status = runner.run()
    assert [(o["symbol"], o["side"]) for o in broker.posts] == [("SPY", "buy"), ("UP", "buy"), ("DOWN", "sell")]
    assert "1 of 2 ETFs above" in status["etf_trend"]["note"] and "2 breakouts" in status["momentum"]["note"]
    assert paper.store.storage.get("strategy-status")["momentum"]["orders"][0]["result"] == "submitted"
    runner.run()  # the same bar and day never submit twice
    assert len(broker.posts) == 3
    broker.fill()
    assert len(paper.maintain(NOW)) == 3

    # UP falls back inside its noise band on the next bar: momentum asks the monitor to close it.
    broker.now = data.quoted_at = NOW + timedelta(minutes=30)
    data.today = {**TODAY, "UP": [*TODAY["UP"], 100.3]}
    runner.run()
    intents = {i["symbol"]: i for i in paper.open_intents()}
    assert "noise band" in intents["UP"]["exit_requested"] and "exit_requested" not in intents["DOWN"]
    for _ in range(3):  # cancel the stop, sell at the market, then confirm flat
        paper.maintain(broker.now)
    assert broker.posts[-1] == dict(symbol="UP", qty="9", side="sell", type="market", time_in_force="day",
                                    extended_hours=False, client_order_id=broker.posts[-1]["client_order_id"])
    assert {i["symbol"] for i in paper.open_intents()} == {"SPY", "DOWN"}

    # Momentum is flat before the close; the ETF trend position is held overnight.
    broker.now = DAY1.close - timedelta(minutes=4)
    for _ in range(3):
        paper.maintain(broker.now)
    assert (broker.posts[-1]["symbol"], broker.posts[-1]["side"], broker.posts[-1]["qty"]) == ("DOWN", "buy", "10")
    assert [i["symbol"] for i in paper.open_intents()] == ["SPY"] and set(broker.positions) == {"SPY"}

    # Next day SPY closes below its 200-day average: the position is sold at the market.
    broker.now = data.quoted_at = DAY2.open + timedelta(minutes=91)
    data.daily = {"SPY": (100, 90), "TLT": (100, 90)}
    paper.maintain(broker.now)
    assert len(broker.posts) == 5
    runner.run()
    paper.maintain(broker.now)
    assert (broker.posts[-1]["symbol"], broker.posts[-1]["side"], broker.posts[-1]["type"]) == ("SPY", "sell", "market")
    assert broker.posts[-1]["qty"] == broker.posts[0]["qty"]
    paper.maintain(broker.now)
    assert not paper.open_intents() and not broker.positions


def test_momentum_skips_symbols_held_by_another_strategy(paper):
    broker, data = paper.client, Data()
    data.asks = {"SPY": 600.0, "UP": 101.5, "DOWN": 99.01}
    paper.submit([Signal("news", "news-UP", "UP", 1, stop_pct=.01, target_r=2)], {"UP": quote(101.5)}, NOW)
    paper.limits["momentum"]["max_positions"] = paper.cfg.strategies.momentum.max_positions = 1
    runner = StrategyRunner(paper.cfg, data, paper, paper.store.storage)
    runner.clock = lambda: broker.now
    status = runner.run()
    # UP is the strongest breakout but belongs to the news strategy, so the one free slot goes to DOWN.
    assert status["momentum"]["orders"] == [dict(symbol="DOWN", side="short", result="submitted")]


def test_momentum_trades_only_its_configured_symbols(paper):
    broker, data = paper.client, Data()
    data.asks = {"SPY": 600.0, "UP": 101.5, "DOWN": 99.01}
    paper.cfg.strategies.etf_trend.enabled = False
    paper.cfg.strategies.momentum.symbols = ["DOWN", "QQQ"]  # QQQ is not on this watchlist
    runner = StrategyRunner(paper.cfg, data, paper, paper.store.storage)
    runner.clock = lambda: broker.now
    status = runner.run()
    assert status["momentum"]["orders"] == [dict(symbol="DOWN", side="short", result="submitted")]
    assert "1 breakouts in 1 symbols" in status["momentum"]["note"]

    # A position opened under an earlier symbol list is closed rather than left without its noise-band exit.
    broker.fill()
    paper.maintain(NOW)
    paper.cfg.strategies.momentum.symbols = ["QQQ"]
    assert "no momentum symbols" in runner.run()["momentum"]["note"]
    assert [o["symbol"] for o in broker.posts] == ["DOWN"]
    assert paper.open_intents("momentum")[0]["exit_requested"] == "removed from the momentum symbols"
    assert AppConfig(strategies={"momentum": {"symbols": [" spy", "SPY", ""]}}).strategies.momentum.symbols == ["SPY"]


def test_runner_waits_for_market_and_execution_readiness(paper):
    runner = StrategyRunner(paper.cfg, Data(), paper, paper.store.storage)
    runner.clock = lambda: datetime(2025, 1, 4, 16, tzinfo=UTC)  # Saturday
    for strategy in (paper.cfg.strategies.etf_rotation, paper.cfg.strategies.cash_park, paper.cfg.strategies.orb,
                     paper.cfg.strategies.vwap_reversion, paper.cfg.strategies.pullback):
        strategy.enabled = True
    assert {s["note"] for s in runner.run().values()} == {"market closed"}
    runner.clock = lambda: NOW
    paper.store.storage.put("event-execution-state", {"phase": "error"})
    assert {s["note"] for s in runner.run().values()} == {"paper execution not ready"}
    assert not paper.client.posts


def test_rotation_ranks_by_momentum_and_keeps_holdings_inside_the_buffer():
    frame = trending(NOW, {"A": .40, "B": .30, "C": .20, "D": .10, "E": -.10})
    assert rotation_members(frame, set(), 2, 3, 200) == ["A", "B"]
    # C is outside the top two but inside the top three, so it is kept and only one place is left to fill.
    assert rotation_members(frame, {"C"}, 2, 3, 200) == ["C", "A"]
    assert rotation_members(frame, {"D"}, 2, 3, 200) == ["A", "B"]  # D fell out of the top three
    # Falling ETFs never qualify, however few others there are; a short history is left out altogether.
    assert rotation_members(frame[frame["symbol"].isin(["D", "E"])], {"E"}, 2, 3, 200) == ["D"]
    assert rotation_members(trending(NOW, {"A": .40}, periods=252), set(), 2, 3, 200) == []
    # A strong year does not qualify an ETF that has since dropped below its average.
    broken = frame[frame["symbol"] == "A"].copy()
    broken.iloc[-1, broken.columns.get_loc("close")] = 115.0
    assert rotation_members(broken, {"A"}, 2, 3, 200) == []


def overnight(paper, data, **enabled):
    """A runner with intraday momentum off and the named overnight strategies switched on."""
    strategies = paper.cfg.strategies
    strategies.momentum.enabled = False
    for name, value in enabled.items():
        for field, setting in dict(value, enabled=True).items():
            setattr(getattr(strategies, name), field, setting)
    paper.limits.update(paper.cfg.limits())
    runner = StrategyRunner(paper.cfg, data, paper, paper.store.storage)
    runner.clock = lambda: paper.client.now
    return runner


def settle(paper):
    for _ in range(3):  # sell at the market, then confirm flat
        paper.maintain(paper.client.now)


def test_rotation_takes_its_etfs_from_the_trend_strategy_and_hands_them_back(paper):
    broker, data = paper.client, Data()
    data.gains = {"SPY": .30, "TLT": .10}
    runner = overnight(paper, data, etf_rotation=dict(top=1, keep=1))

    status = runner.run()
    # SPY leads, so rotation holds it at 40% of equity and trend holds only TLT at its 25%.
    assert [(o["symbol"], o["side"], round(float(o["qty"]) * float(o["limit_price"]), -2)) for o in broker.posts] == [
        ("SPY", "buy", 4000), ("TLT", "buy", 2500)]
    assert "SPY" in status["etf_rotation"]["note"]
    broker.fill()
    runner.run()
    assert len(broker.posts) == 2
    assert {i["symbol"]: i["strategy"] for i in paper.open_intents()} == {"SPY": "etf_rotation", "TLT": "etf_trend"}

    # The next day TLT leads: each strategy sells, then buys the other ETF once the symbol is free.
    broker.now = data.quoted_at = DAY2.open + timedelta(minutes=91)
    data.gains = {"SPY": .10, "TLT": .50}
    runner.run()
    reasons = {i["symbol"]: i["exit_requested"] for i in paper.open_intents()}
    assert "momentum ranking" in reasons["SPY"] and "handed to the rotation" in reasons["TLT"]
    assert [(o["symbol"], o["side"], o["type"]) for o in broker.posts[2:]] == [
        ("SPY", "sell", "market"), ("TLT", "sell", "market")]
    settle(paper)
    assert not paper.open_intents()
    runner.run()
    assert [(o["symbol"], o["side"], round(float(o["qty"]) * float(o["limit_price"]), -2))
            for o in broker.posts[4:]] == [("TLT", "buy", 4000), ("SPY", "buy", 2500)]
    assert {i["symbol"]: i["strategy"] for i in paper.open_intents()} == {"TLT": "etf_rotation", "SPY": "etf_trend"}


def test_idle_cash_is_parked_and_resized_around_the_overnight_positions(paper):
    broker, data = paper.client, Data()
    runner = overnight(paper, data, cash_park={})

    def parked():
        return [round(float(o["qty"]) * float(o["limit_price"]), -2) for o in broker.posts
                if o["symbol"] == "BOXX" and o["side"] == "buy"]

    # Trend holds SPY at 25% of equity and 5% stays in cash, so 70% goes to the Treasury-bill ETF.
    status = runner.run()
    assert [o["symbol"] for o in broker.posts] == ["SPY", "BOXX"] and parked() == [7000]
    assert "target 70%" in status["cash_park"]["note"]
    broker.fill()
    assert runner.run()["cash_park"]["note"] == "70% of equity in BOXX, target 70%"
    assert len(broker.posts) == 2

    # Another overnight position leaves the parked cash too large. It is sold at once, so the account
    # does not borrow, and bought again at the smaller size on the next day.
    paper.submit([Signal("etf_rotation", "etf_rotation-UP", "UP", 1, intraday=False, fractional=True)],
                 {"UP": quote(100.0)}, NOW)
    broker.fill()
    assert "resizing" in runner.run()["cash_park"]["note"]
    settle(paper)
    assert (broker.posts[-1]["symbol"], broker.posts[-1]["side"], broker.posts[-1]["type"]) == ("BOXX", "sell", "market")
    runner.run()
    assert parked() == [7000] and {i["symbol"] for i in paper.open_intents()} == {"SPY", "UP"}
    broker.now = data.quoted_at = DAY2.open + timedelta(minutes=91)
    runner.run()
    assert parked() == [7000, 6200]

    # The parking ETF cannot also be a watchlist symbol, and nothing is parked before the opening orders.
    broker.now = DAY2.open + timedelta(minutes=10)
    assert "waiting" in StrategyRunner(paper.cfg, data, paper, paper.store.storage)._cash_park(
        broker.now, CAL.session_for(broker.now), ["SPY"])
    assert "on the watchlist" in runner._cash_park(broker.now, CAL.session_for(broker.now), ["SPY", "BOXX"])


STRETCH = {"UP": [100.0] * 9 + [99.0], "FLAT": [100.0] * 10}  # UP drops one point after 40 quiet minutes


def opening_history(symbols=("UP", "DOWN")):
    sessions = CAL.sessions_between(datetime(2024, 12, 10).date(), datetime(2025, 1, 6).date())
    return pd.concat([bars(s, {symbol: [100.0] * 27 for symbol in symbols}, timedelta(minutes=15)) for s in sessions])


OPENING = {"UP": [100.0, 100.5, 101.0, 101.2, 101.5], "DOWN": [100.0, 99.5, 99.0, 98.8, 98.9]}


def pullbacks(end):
    """UP: a long uptrend with a sharp two-day dip; DOWN: still rising; FLAT: a dip in a downtrend."""
    frame = trending(end, {"UP": .2, "DOWN": .2, "FLAT": -.2})
    for symbol, closes in (("UP", [116.0, 113.0]), ("FLAT", [78.0, 76.0])):
        rows = frame.index[frame["symbol"] == symbol][-2:]
        frame.loc[rows, "close"] = closes
    return frame


def test_vwap_orb_and_pullback_signals():
    at = DAY1.open + 9 * FAST
    states = vwap_states(bars(DAY1, STRETCH, FAST), at, CAL, 2)
    assert set(states) == {"UP"}  # FLAT never moved, so it has no deviation to measure
    up = states["UP"]
    assert up["side"] == 1 and up["strength"] > 4 and up["stop"] == pytest.approx(99 - (up["vwap"] - 99) / up["strength"])
    assert not up["long_reverted"] and up["short_reverted"]
    assert vwap_states(bars(DAY1, STRETCH, FAST), DAY1.open + 5 * FAST, CAL, 2) == {}  # under half an hour
    assert vwap_states(bars(DAY1, STRETCH, FAST), at, CAL, 5)["UP"]["side"] == 0

    average = opening_volume(opening_history(), 14, CAL)
    assert average.to_dict() == {"DOWN": 1000, "UP": 1000}
    assert opening_volume(opening_history(), 30, CAL).empty
    orb = orb_states(average, bars(DAY1, OPENING, FAST), DAY1.open + 4 * FAST, CAL)
    assert (orb["UP"]["side"], orb["UP"]["stop"], orb["UP"]["rvol"]) == (1, 100.0, 3.0)
    assert (orb["DOWN"]["side"], orb["DOWN"]["stop"]) == (0, 100.0)  # falling range, but no close below 98.8 yet
    assert orb_states(average, bars(DAY1, OPENING, FAST), DAY1.open + 3 * FAST, CAL) == {}  # range still forming

    states = pullback_states(pullbacks(NOW), 200, 10, 5)
    assert states["UP"]["state"] == "buy" and states["UP"]["rsi"] < 10
    assert states["DOWN"]["state"] == "sell" and states["FLAT"]["state"] is None


def intraday(paper, data, name, **settings):
    runner = overnight(paper, data, **{name: settings})
    paper.cfg.strategies.etf_trend.enabled = False
    return runner


def test_vwap_reversion_fades_a_stretch_and_exits_back_at_vwap(paper):
    broker, data = paper.client, Data()
    data.fast, data.asks = bars(DAY1, STRETCH, FAST), {"UP": 99.0}
    broker.now = data.quoted_at = DAY1.open + 9 * FAST + timedelta(seconds=30)
    runner = intraday(paper, data, "vwap_reversion", symbols=["UP", "FLAT"])
    status = runner.run()
    (order,) = broker.posts
    assert (order["symbol"], order["side"], order["order_class"]) == ("UP", "buy", "oto")
    assert float(order["stop_loss"]["stop_price"]) < 99 and "1 orders submitted" in status["vwap_reversion"]["note"]
    intent = paper.open_intents("vwap_reversion")[0]
    setup = intent["context"]
    assert setup["at"] == (DAY1.open + 9 * FAST).isoformat()
    assert setup["price"] < setup["levels"]["Lower band"] < setup["levels"]["Session VWAP"]
    deadline = datetime.fromisoformat(intent["deadline"]) - broker.now
    assert timedelta(minutes=60) <= deadline < timedelta(minutes=60, seconds=1)
    runner.run()
    assert len(broker.posts) == 1  # one trade per symbol a day
    broker.fill()
    paper.maintain(broker.now)

    broker.now = data.quoted_at = broker.now + FAST
    data.fast = bars(DAY1, {"UP": [*STRETCH["UP"], 100.0]}, FAST)
    runner.run()
    assert paper.open_intents("vwap_reversion")[0]["exit_requested"] == "price back at VWAP"

    broker.now = DAY1.close - timedelta(minutes=50)  # too late in the day for a 60-minute reversion
    assert "no entries" in runner.run()["vwap_reversion"]["note"]


def test_orb_buys_a_high_volume_breakout_and_stops_entering_later(paper):
    broker, data = paper.client, Data()
    data.fast, data.opening, data.asks = bars(DAY1, OPENING, FAST), opening_history(), {"UP": 101.5}
    broker.now = data.quoted_at = DAY1.open + 4 * FAST + timedelta(seconds=30)
    runner = intraday(paper, data, "orb")
    status = runner.run()
    assert [(o["symbol"], o["side"], o["stop_loss"]["stop_price"]) for o in broker.posts] == [("UP", "buy", "100.0")]
    assert "2 in play" in status["orb"]["note"] and "1 breaking out" in status["orb"]["note"]
    assert paper.open_intents("orb")[0]["deadline"] == (DAY1.close - timedelta(minutes=5)).isoformat()

    # A falling opening range is shorted once a close breaks below its low, with the stop at the range high.
    broker.now = data.quoted_at = broker.now + FAST
    data.fast, data.asks = bars(DAY1, {**OPENING, "DOWN": [*OPENING["DOWN"], 98.5]}, FAST), {"DOWN": 98.5}
    runner.run()
    assert [(o["symbol"], o["side"], o["stop_loss"]["stop_price"]) for o in broker.posts[1:]] == [("DOWN", "sell", "100.0")]
    assert paper.open_intents("orb")[1]["side"] == "short"

    broker.now = DAY1.open + timedelta(minutes=121)
    assert "entries closed" in runner.run()["orb"]["note"]


def test_pullback_buys_an_oversold_uptrend_and_sells_the_bounce(paper):
    broker, data = paper.client, Data()
    data.daily_frame, data.asks = pullbacks(NOW), {"UP": 113.0}
    runner = intraday(paper, data, "pullback")
    status = runner.run()
    (order,) = broker.posts
    assert (order["symbol"], order["side"]) == ("UP", "buy") and "order_class" not in order
    assert float(order["qty"]) * float(order["limit_price"]) <= 1000  # 10% of equity
    assert "1 of 3 stocks oversold" in status["pullback"]["note"]
    broker.fill()
    paper.maintain(broker.now)
    assert paper.open_intents("pullback")[0]["deadline"] is None  # held overnight

    broker.now = data.quoted_at = DAY2.open + timedelta(minutes=10)
    data.daily_frame = trending(DAY2.open, {"UP": .2})  # back above its 5-day average
    runner.run()
    assert paper.open_intents("pullback")[0]["exit_requested"] == "closed above its 5-day average"
