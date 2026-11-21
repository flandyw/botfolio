from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from botfolio.config import AppConfig, load_config
from botfolio.data.calendar import frame_to_bars, get_calendar
from botfolio.data.models import Bar
from botfolio.data.synthetic import random_walk_bars

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


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch, tmp_path):
    """Tests must never load deployment keys or make external broker/model requests."""
    monkeypatch.setenv("BOTFOLIO_ENV_FILE", str(tmp_path / "no-deployment-secrets.env"))
    for name in ("ALPACA_KEY", "ALPACA_SECRET", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "EVENT_AI_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("EVENT_AI_PROVIDER", "disabled")
    monkeypatch.setenv("EVENT_AI_MODEL", "disabled")
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "false")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "false")
    def blocked_request(*args, **kwargs):
        raise AssertionError("External services must be mocked in tests")
    monkeypatch.setattr("requests.sessions.Session.request", blocked_request)
