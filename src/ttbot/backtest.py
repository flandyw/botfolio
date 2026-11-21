"""Historical backtest: replays cached Alpaca minute bars through the exact same engine used live."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date
from typing import Any

import pandas as pd

from ttbot.analysis import compute_stats
from ttbot.config import AppConfig
from ttbot.data.alpaca import AlpacaClient
from ttbot.data.calendar import frame_to_bars, get_calendar
from ttbot.data.loader import load_history
from ttbot.engine import Engine, EngineOutput
from ttbot.storage import Storage
from ttbot.strategies import build_roster

log = logging.getLogger(__name__)


def run_backtest(
    cfg: AppConfig,
    storage: Storage,
    client: AlpacaClient | None,
    start: date,
    end: date,
    run_id: str | None = None,
    bars_df: pd.DataFrame | None = None,
    monte_carlo: bool = True,
    notes: str | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    cal = get_calendar(cfg.session.calendar)
    run_id = run_id or f"bt-{start}-{end}-{cfg.data.feed}-{uuid.uuid4().hex[:6]}"
    if bars_df is None:
        bars_df = load_history(client, storage, cal, cfg.symbols, start, end, cfg.data.feed, cfg.data.adjustment)
    bars = frame_to_bars(bars_df, cal)
    if not bars:
        raise RuntimeError("no bars available for the requested range")
    storage.delete_run(run_id)
    storage.create_run(run_id, "backtest", cfg.model_dump(), str(start), str(end))
    storage.update_run(run_id, notes=notes or f"{len(bars)} bars, feed={cfg.data.feed}")

    t0 = time.perf_counter()
    engine = Engine(cfg, build_roster(cfg))
    out = EngineOutput()
    for i, bar in enumerate(bars):
        engine.process(bar)
        if i % 20000 == 0:
            out.extend(engine.drain())
    engine.end_session()
    out.extend(engine.drain())
    log.info("backtest simulated", extra={"run_id": run_id, "bars": len(bars), "trades": len(out.trades),
                                          "seconds": round(time.perf_counter() - t0, 1)})
    storage.save_trades(run_id, out.trades)
    storage.save_signals(run_id, out.signals)
    storage.save_equity(run_id, out.equity)

    rejections = {n: p.rejections for n, p in engine.portfolios.items() if p.rejections}
    stats = compute_stats(
        cfg, storage.trades(run_id), storage.equity(run_id), list(engine.portfolios), bars if monte_carlo else None,
        exits={n: p.exits for n, p in engine.portfolios.items()},
        symbols={s.name: s.symbols for s in engine.strategies},
    )
    for name, rej in rejections.items():
        stats[name]["rejections"] = rej
    storage.save_stats(run_id, stats)
    storage.update_run(run_id, status="complete")
    return run_id, stats
