"""Synthetic random-walk minute bars (no edge by construction) for tests and offline demos.

On a driftless random walk no chart pattern can have predictive power, so every pattern strategy should
lose roughly its trading costs. This doubles as a sanity check for look-ahead bugs: a strategy that
"wins" on synthetic data is cheating.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ttbot.data.calendar import MarketCalendar


def random_walk_bars(
    calendar: MarketCalendar,
    symbols: list[str],
    start: date,
    end: date,
    seed: int = 7,
    start_price: float = 400.0,
    vol_per_min: float = 0.0006,
    drop_prob: float = 0.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for k, sym in enumerate(symbols):
        price = start_price * (1 + 0.25 * k)
        for s in calendar.sessions_between(start, end):
            minutes = int((s.close - s.open).total_seconds() // 60)
            price *= float(np.exp(rng.normal(0, vol_per_min * 8)))  # overnight gap
            for m in range(minutes):
                if drop_prob and rng.random() < drop_prob:
                    continue
                path = price * np.exp(np.cumsum(rng.normal(0, vol_per_min / 2, 4)))
                o, c = price, float(path[-1])
                h = max(o, c, float(path.max())) * (1 + abs(rng.normal(0, vol_per_min / 4)))
                lo = min(o, c, float(path.min())) * (1 - abs(rng.normal(0, vol_per_min / 4)))
                rows.append((sym, s.open + pd.Timedelta(minutes=m), o, h, lo, c, float(rng.integers(100, 5000))))
                price = c
    df = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values(["ts", "symbol"]).reset_index(drop=True)
