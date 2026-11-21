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
from botfolio.strategies import StrategyRunner, momentum_states, noise_profile, trend_states

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
                                                       qty=order["qty"] if long else "-" + order["qty"])


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


def bars(session, moves):
    """30-minute bars for one session: {symbol: [close per bar]}; each bar opens at the previous close."""
    rows = []
    for symbol, closes in moves.items():
        opened = closes[0]
        for i, close in enumerate(closes[1:]):
            if session.open + i * timedelta(minutes=30) < session.close:
                rows.append((symbol, session.open + i * timedelta(minutes=30), opened, max(opened, close),
                             min(opened, close), close, 1000))
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


def test_paused_entries_reach_no_strategy(paper):
    paper.store.storage.put("event-paper-armed", False)
    signal = Signal("etf_trend", "etf_trend-SPY", "SPY", 1, intraday=False, fractional=True)
    assert paper.submit([signal], {"SPY": quote(600.0)}, NOW) == {signal.key: "paper entries paused in dashboard"}
    assert not paper.client.posts


class Data:
    def __init__(self):
        self.daily, self.today, self.asks = {"SPY": (100, 110), "TLT": (100, 90)}, TODAY, {}
        self.quoted_at = NOW

    def get_bars(self, symbols, start, end, timeframe, feed, adjustment):
        if timeframe == "1Day":
            frame = daily(end, self.daily)
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


def test_runner_waits_for_market_and_execution_readiness(paper):
    runner = StrategyRunner(paper.cfg, Data(), paper, paper.store.storage)
    runner.clock = lambda: datetime(2025, 1, 4, 16, tzinfo=UTC)  # Saturday
    assert {s["note"] for s in runner.run().values()} == {"market closed"}
    runner.clock = lambda: NOW
    paper.store.storage.put("event-execution-state", {"phase": "error"})
    assert {s["note"] for s in runner.run().values()} == {"paper execution not ready"}
    assert not paper.client.posts
