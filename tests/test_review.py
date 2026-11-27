from datetime import UTC, datetime, timedelta

from botfolio.events.review import score

T0 = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)  # 10:00 ET


def test_score_signs_moves_by_the_call_and_judges_held_back_calls_the_other_way():
    times = [T0 + timedelta(minutes=m) for m in range(0, 70)]
    closes = [100 - .01 * m for m in range(70)]  # drifts down 0.6% over the hour
    short = dict(received=T0 - timedelta(seconds=30), side=-1, held=False)
    s = score(short, times, closes)
    assert s["entry"] == 100 and s["verdict"] == "right" and s["m60"] > 0 and s["bracket"] == "time"
    assert s["full_hour"] and s["entry_time"] == int(datetime(2026, 11, 25, 10, 0, tzinfo=UTC).timestamp())
    assert score({**short, "side": 1}, times, closes)["verdict"] == "wrong"
    assert score({**short, "side": 1, "held": True}, times, closes)["verdict"] == "right"  # skipping a loser
    crash = [100, 97.5] + [97.5] * 68
    assert score({**short, "side": 1}, times, crash)["bracket"] == "stop"
    assert score(dict(received=T0 + timedelta(hours=2), side=1, held=False), times, closes) is None
