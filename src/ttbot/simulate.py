"""Local simulated-execution test of the forward (live) path.

Replays one real session minute-by-minute through ``LiveRunner`` with a simulated clock, exactly as the
forward loop would see it (only completed bars, polled each minute). Midway it simulates a crash: the
runner object is discarded and a new one recovers from the SQLite snapshot. The run is then repeated
without a crash in an in-memory database and the two trade lists must be identical.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from ttbot.config import AppConfig
from ttbot.data.alpaca import AlpacaClient
from ttbot.data.calendar import get_calendar
from ttbot.data.loader import load_history
from ttbot.data.synthetic import random_walk_bars
from ttbot.live import LiveRunner
from ttbot.storage import Storage

log = logging.getLogger(__name__)


class SimClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def _replay(cfg: AppConfig, storage: Storage, df: pd.DataFrame, day: date, run_id: str, crash_at: int) -> pd.DataFrame:
    cal = get_calendar(cfg.session.calendar)
    sess = cal.session(day)
    assert sess is not None
    clock = SimClock(sess.open - timedelta(minutes=5))

    def fetch(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
        m = (df["ts"] >= start) & (df["ts"] < end) & df["symbol"].isin(symbols)
        return df[m].copy()

    def new_runner() -> LiveRunner:
        r = LiveRunner(cfg, storage, fetch, run_id=run_id, clock=clock)
        r.start()
        return r

    storage.delete_run(run_id)
    storage.put(f"engine:{run_id}", None)
    runner = new_runner()
    minute = 0
    end = sess.close + timedelta(minutes=3)
    while clock.t <= end:
        clock.t += timedelta(minutes=1)
        runner.step()
        minute += 1
        if crash_at and minute == crash_at:
            log.info("simulating crash + recovery", extra={"minute": minute})
            del runner
            runner = new_runner()
    runner.refresh_stats()
    storage.update_run(run_id, status="complete", notes=f"simulated forward session {day}")
    return storage.trades(run_id)


def run_simulation(
    cfg: AppConfig,
    storage: Storage,
    client: AlpacaClient | None,
    day: date,
    synthetic: bool = False,
    crash_at_minute: int = 120,
) -> bool:
    cal = get_calendar(cfg.session.calendar)
    if cal.session(day) is None:
        raise ValueError(f"{day} is not a trading session")
    start = day - timedelta(days=14)
    if synthetic:
        df = random_walk_bars(cal, cfg.symbols, start, day, seed=cfg.seed)
    else:
        df = load_history(client, storage, cal, cfg.symbols, start, day, cfg.data.feed, cfg.data.adjustment,
                          now=datetime.now(UTC))
    run_id = f"sim-{day}{'-synthetic' if synthetic else ''}"
    crashed = _replay(cfg, storage, df, day, run_id, crash_at_minute)
    clean = _replay(cfg, Storage(":memory:"), df, day, run_id, 0)
    cols = ["trade_id", "side", "qty", "entry_price", "exit_price", "net_pnl", "exit_reason"]
    a = crashed[cols].sort_values("trade_id").reset_index(drop=True)
    b = clean[cols].sort_values("trade_id").reset_index(drop=True)
    identical = a.round(6).equals(b.round(6))
    print(f"simulated session {day}: {len(crashed)} trades across {crashed['portfolio'].nunique()} portfolios")
    print(f"crash at minute {crash_at_minute} + recovery reproduced the uninterrupted run exactly: {identical}")
    if len(crashed):
        summary = crashed.groupby("portfolio").agg(trades=("net_pnl", "size"), net_pnl=("net_pnl", "sum"))
        print(summary.sort_values("net_pnl", ascending=False).to_string(float_format="{:,.2f}".format))
    print(f"\nrun_id: {run_id} (open it in the dashboard)")
    return identical
