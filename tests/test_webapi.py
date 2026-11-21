from datetime import date

import pandas as pd

from botfolio.webapi import _clean, chart_time, regular_hours, resample


def _df(cal, day=date(2026, 9, 15), minutes=range(-5, 400)):
    s = cal.session(day)
    ts = [s.open + pd.Timedelta(minutes=m) for m in minutes]
    return pd.DataFrame({"symbol": "SPY", "ts": pd.to_datetime(ts, utc=True), "open": 1.0, "high": 2.0, "low": 0.5,
                         "close": 1.5, "volume": 10.0})


def test_chart_time_is_new_york_wall_clock(cal):
    open_ = cal.session(date(2026, 9, 15)).open  # 13:30 UTC = 09:30 EDT
    assert chart_time(open_) % 86400 == 9 * 3600 + 30 * 60


def test_regular_hours_and_resample_anchor_to_the_open(cal):
    df = regular_hours(_df(cal), cal)
    assert len(df) == 390
    h = resample(df, "60")
    assert [t % 86400 // 60 for t in chart_time(h["ts"])][:2] == [570, 630]  # 09:30, 10:30
    assert h["volume"].sum() == 3900 and len(h) == 7  # 6.5 hours -> last bar is the 15:30 half hour
    d = resample(df, "D")
    assert len(d) == 1 and d["open"].iloc[0] == 1.0 and d["high"].iloc[0] == 2.0


def test_early_close_is_respected(cal):
    assert len(regular_hours(_df(cal, date(2026, 11, 27)), cal)) == 210  # day after Thanksgiving, 13:00 close


def test_clean_replaces_nan():
    assert _clean({"a": float("nan"), "b": [1.0, float("inf")]}) == {"a": None, "b": [1.0, None]}
