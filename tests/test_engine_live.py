from datetime import date, timedelta

from ttbot.backtest import run_backtest
from ttbot.engine import Engine
from ttbot.simulate import _replay
from ttbot.storage import Storage
from ttbot.strategies import build_roster


def test_backtest_end_to_end_and_persistence(cfg, rw_df):
    st = Storage(":memory:")
    run_id, stats = run_backtest(cfg, st, None, date(2026, 8, 3), date(2026, 8, 21), "t", rw_df, monte_carlo=True)
    trades = st.trades(run_id)
    assert len(trades) > 100
    assert set(stats) == {s.name for s in build_roster(cfg)}
    # benchmark bought once and held
    assert stats["buy_hold_SPY"]["n_trades"] == 0 and stats["buy_hold_SPY"]["n_days"] == 15
    # all intraday trades closed within their session
    same_day = (trades["entry_ts"].dt.tz_convert("America/New_York").dt.date
                == trades["exit_ts"].dt.tz_convert("America/New_York").dt.date)
    assert same_day.all()
    assert (trades["entry_ts"] <= trades["exit_ts"]).all()
    for name, s in stats.items():
        if s["group"] != "benchmark" and s["n_trades"] >= 3:
            if name.removeprefix("inv_").startswith("noise_"):
                assert "p_mc" not in s  # strategy-driven exits: no stop/target null model
            else:
                assert 0 <= s["p_mc"] <= 1


def test_backtest_is_deterministic(cfg, rw_df):
    a = run_backtest(cfg, Storage(":memory:"), None, date(2026, 8, 3), date(2026, 8, 7), "a", rw_df, False)[1]
    b = run_backtest(cfg, Storage(":memory:"), None, date(2026, 8, 3), date(2026, 8, 7), "b", rw_df, False)[1]
    assert {k: v.get("total_net_pnl", None) for k, v in a.items()} == \
           {k: v.get("total_net_pnl", None) for k, v in b.items()}


def test_engine_pickle_roundtrip_continues_identically(cfg, rw_bars):
    import pickle

    e1 = Engine(cfg, build_roster(cfg))
    e2 = Engine(cfg, build_roster(cfg))
    half = len(rw_bars) // 2
    for b in rw_bars[:half]:
        e1.process(b)
        e2.process(b)
    e2 = pickle.loads(pickle.dumps(e2))
    for b in rw_bars[half:]:
        e1.process(b)
        e2.process(b)
    t1 = sorted((t.trade_id, round(t.net_pnl, 6)) for t in e1.drain().trades)
    t2 = sorted((t.trade_id, round(t.net_pnl, 6)) for t in e2.drain().trades)
    assert t1 == t2 and len(t1) > 50


def test_live_runner_crash_recovery_matches_uninterrupted(cfg, rw_df):
    day = date(2026, 8, 20)
    a = _replay(cfg, Storage(":memory:"), rw_df, day, "sim", crash_at=95)
    b = _replay(cfg, Storage(":memory:"), rw_df, day, "sim", crash_at=0)
    cols = ["trade_id", "net_pnl", "exit_reason"]
    assert len(a) > 10
    assert a[cols].sort_values("trade_id").reset_index(drop=True).equals(
        b[cols].sort_values("trade_id").reset_index(drop=True))


def test_engine_ignores_duplicate_bars(cfg, rw_bars):
    e = Engine(cfg, build_roster(cfg))
    assert e.process(rw_bars[0]) is True
    assert e.process(rw_bars[0]) is False


def test_live_matches_backtest_trades(cfg, rw_df, cal):
    """Forward mode (polling, completed bars only) and backtest must produce the same trades for a session."""
    from ttbot.data.calendar import frame_to_bars

    day = date(2026, 8, 19)
    live = _replay(cfg, Storage(":memory:"), rw_df, day, "sim", crash_at=0)
    # backtest with the same warm-up procedure: the longest strategy warm-up, then fresh portfolios
    e = Engine(cfg, build_roster(cfg))
    n_warm = max([3, *(s.warmup_sessions for s in e.strategies)])
    sess = cal.sessions_between(day - timedelta(days=2 * n_warm + 10), day - timedelta(days=1))[-n_warm:]
    bars = frame_to_bars(rw_df, cal)
    for b in bars:
        if sess[0].day <= b.session < day:
            e.process(b)
    e.end_session()
    e.drain()
    from ttbot.execution.portfolio import Portfolio

    for n, p in list(e.portfolios.items()):
        e.portfolios[n] = Portfolio(n, p.timeframe, p.risk, p.exits, p.costs)
    for s in e.strategies:
        if s.kind == "buy_hold":
            s._states.clear()
    for b in bars:
        if b.session == day:
            e.process(b)
    e.end_session()
    bt = sorted((t.trade_id, round(t.net_pnl, 6)) for t in e.drain().trades)
    lv = sorted(zip(live["trade_id"], live["net_pnl"].round(6), strict=True))
    assert bt == lv
