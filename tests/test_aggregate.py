import pandas as pd

from botfolio.data.aggregate import BarAggregator


def test_5m_aggregation_matches_pandas_resample(rw_bars):
    bars = [b for b in rw_bars if b.symbol == "SPY"]
    agg = BarAggregator(5)
    out = []
    for b in bars:
        if (d := agg.flush_before(b)) is not None:
            out.append(d)
        if (d := agg.add(b)) is not None:
            out.append(d)
    df = pd.DataFrame([(b.ts, b.open, b.high, b.low, b.close) for b in bars], columns=["ts", "o", "h", "l", "c"])
    ref = df.set_index("ts").resample("5min", origin="epoch").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    got = pd.DataFrame([(b.ts, b.open, b.high, b.low, b.close) for b in out], columns=["ts", "o", "h", "l", "c"]).set_index("ts")
    assert len(got) == len(ref)
    pd.testing.assert_frame_equal(got, ref, check_freq=False, check_names=False)


def test_window_never_emitted_before_complete(rw_bars):
    agg = BarAggregator(5)
    for b in [b for b in rw_bars if b.symbol == "QQQ"][:500]:
        flushed = agg.flush_before(b)
        if flushed is not None:
            assert flushed.end <= b.ts  # only emitted when a later minute proves it complete
        done = agg.add(b)
        if done is not None:
            assert done.end == b.end or done.is_last
