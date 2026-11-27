from datetime import UTC, datetime

from botfolio.events import risk


class Store(dict):
    put = dict.__setitem__


def test_scale_defaults_to_max_and_reads_today():
    now = datetime(2026, 11, 30, 15, tzinfo=UTC)
    store = Store()
    assert risk.scale(store, now) == 1.0
    store.put("risk-regime:2026-11-30", {"risk_scale": 0.25})
    assert risk.scale(store, now) == 0.25
    assert risk.scale(store, datetime(2026, 12, 1, 15, tzinfo=UTC)) == 1.0  # yesterday's call does not carry over
