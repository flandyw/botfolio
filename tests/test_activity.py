from botfolio.events.activity import strategy_performance


class Broker:
    def __init__(self, orders, positions):
        self.orders, self.positions = orders, positions

    def request(self, method, path, params=None):
        return self.orders if path == "orders" else self.positions


def order(cid, price, qty, legs=()):
    return dict(client_order_id=cid, filled_avg_price=str(price), filled_qty=str(qty), filled_at=None,
                submitted_at="2026-11-25T15:00:00Z", legs=list(legs))


def test_strategy_performance_splits_realized_and_open_pnl():
    rows = [
        dict(key="a" * 50, client_id="evt-a", strategy="news", symbol="X", side="long", opened="2026-11-25T15:00:00+00:00"),
        dict(key="b", client_id="evt-b", strategy="momentum", symbol="Q", side="short", opened="2026-11-25T15:00:00+00:00"),
        dict(key="c", client_id="evt-c", strategy="etf_trend", symbol="E", side="long", opened="2026-11-25T15:00:00+00:00"),
        dict(key="d", client_id="evt-d", strategy="news", symbol="Y", side="long", opened="2026-11-25T15:00:00+00:00"),
    ]
    broker = Broker([
        order("evt-a", 100, 10, [order("leg", 110, 0), order("leg2", 0, 0)]),
        order("exit-" + "a" * 40, 103, 10),          # strategy exit: +30
        order("evt-b", 50, 4, [order("stop", 52, 4)]),  # short stopped out: -8
        order("evt-c", 20, 5),                       # still held, marked at 22: +10 open
        order("evt-d", 0, 0),                        # never filled: ignored
    ], [dict(symbol="E", current_price="22")])
    result = strategy_performance(broker, rows)
    assert result["news"]["realized"] == 30 and result["news"]["wins"] == 1 and result["news"]["trades"] == 1
    assert result["news"]["pnl_pct"] == 3
    assert result["momentum"]["pnl"] == -8 and result["momentum"]["wins"] == 0 and result["momentum"]["closed"] == 1
    assert result["etf_trend"]["unrealized"] == 10 and result["etf_trend"]["closed"] == 0
