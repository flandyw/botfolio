from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from ttbot.config import AppConfig, load_config
from ttbot.data.calendar import frame_to_bars, get_calendar
from ttbot.data.models import Bar
from ttbot.data.synthetic import random_walk_bars

DAY = date(2026, 9, 15)  # a regular Tuesday session


@pytest.fixture(scope="session")
def cal():
    return get_calendar("XNYS")


@pytest.fixture()
def cfg() -> AppConfig:
    return load_config()


def make_bars(cal, ohlc: list[tuple[float, float, float, float]], day: date = DAY, symbol: str = "SPY",
              start_minute: int = 0) -> list[Bar]:
    s = cal.session(day)
    rows = [
        (symbol, s.open + timedelta(minutes=start_minute + i), o, h, lo, c, 1000.0)
        for i, (o, h, lo, c) in enumerate(ohlc)
    ]
    df = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return frame_to_bars(df, cal)


@pytest.fixture(scope="session")
def rw_df(cal):
    return random_walk_bars(cal, ["QQQ", "SPY"], date(2026, 8, 3), date(2026, 8, 21), seed=11, drop_prob=0.01)


@pytest.fixture(scope="session")
def rw_bars(cal, rw_df):
    return frame_to_bars(rw_df, cal)
