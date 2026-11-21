from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import ValidationError

from botfolio.events.ai import AI, abstain, analyze
from botfolio.events.models import Decision, Event
from botfolio.events.paper import PaperExecutor
from botfolio.events.research import market_context, replay
from botfolio.events.store import EventStore
from botfolio.storage import Storage

AT = datetime(2025, 1, 6, 14, 30, tzinfo=UTC)


def event(**kwargs):
    return Event(**(dict(source="fixture", source_id="1", ticker="SPY", event_type="earnings",
        published_at=AT, content_at=AT, received_at=AT+timedelta(seconds=2),
        source_url="https://example.com/1", text="Revenue increased.", mode="historical",
        snapshot_verified=True) | kwargs))


def decision(e):
    return Decision(ticker=e.ticker, event_type=e.event_type, event_timestamp=e.published_at,
        source_url=e.source_url, fundamental_impact="positive", materiality="high",
        expectations_surprise="positive", uncertainty="low", directional_hypothesis="bullish",
        reasoning="fixture only", key_evidence=["Revenue increased."], risk_factors=[], recommended_action="BUY")


def frame():
    return pd.DataFrame([dict(symbol="SPY", ts=AT+timedelta(minutes=i), open=100., high=100.1,
        low=99.9, close=100., volume=1000) for i in range(390)])


def run(e=None, df=None, latency=30):
    e = e or event()
    return replay([e], {e.key: (decision(e), 1., "fixture", AT)}, frame() if df is None else df,
                  train_end=AT-timedelta(days=3), validation_end=AT-timedelta(days=1),
                  holding=(5,), latency=latency)


@pytest.mark.parametrize("updates", [dict(received_at=AT-timedelta(seconds=1)),
    dict(content_at=AT+timedelta(seconds=3)), dict(published_at=AT.replace(tzinfo=None)),
    dict(consensus_eps=2.), dict(actual_eps=float("nan"))])
def test_invalid_event(updates):
    with pytest.raises(ValidationError):
        event(**updates)


def test_immutable_dedup_and_decision_audit():
    store = EventStore(Storage(":memory:"))
    e = event()
    assert store.add(e)
    assert not store.add(event(received_at=AT+timedelta(days=1)))
    assert store.events()[0].received_at == e.received_at
    assert store.add(event(text="Revised revenue."))
    ai = Mock(model="test", provider="fixture")
    ai.evaluate.return_value = (decision(e), {"evidence": e.text}, {"raw": "yes"})
    analyze(store, e, "frozen", ai, {})
    analyze(store, e, "frozen", ai, {})
    assert ai.evaluate.call_count == 1
    assert len(store.audit()) == 1


def test_decision_rejects_identity_and_invented_evidence():
    e = event()
    with pytest.raises(ValueError):
        decision(e).model_copy(update={"ticker": "NVDA"}).validate_event(e)
    with pytest.raises(ValueError):
        decision(e).model_copy(update={"key_evidence": ["made up"]}).validate_event(e)
    assert abstain(e, "missing").side() == 0
    assert decision(e).model_copy(update={"materiality": "low"}).side() == 0
    assert decision(e).model_copy(update={"uncertainty": "high", "recommended_action": "WAIT"}).side() == 1
    assert decision(e).model_copy(update={"directional_hypothesis": "bearish", "recommended_action": "WAIT"}).side() == -1


def test_replay_latency_costs_and_matched_controls():
    report = run()
    ai = next(r for r in report["trades"] if r["strategy"] == "ai")
    rnd = next(r for r in report["trades"] if r["strategy"] == "random:ai")
    assert ai["entry"] == (AT+timedelta(minutes=1)).isoformat()
    assert ai["exit"] == (AT+timedelta(minutes=6)).isoformat()
    assert ai["net_pnl"] < 0
    assert ai["costs"] > 0
    assert (ai["qty"], ai["entry"], ai["exit"]) == (rnd["qty"], rnd["entry"], rnd["exit"])
    assert ai["split"] == "test"


def test_missing_bars_rejected():
    report = run(df=frame().drop(index=3))
    assert not report["trades"]
    assert any("missing bars" in r["reason"] for r in report["exclusions"])


def test_unverified_revision_rejected():
    report = run(event(snapshot_verified=False))
    assert not report["trades"]
    assert "verified" in report["exclusions"][0]["reason"]


def test_no_future_context():
    from botfolio.data.calendar import frame_to_bars, get_calendar
    bars = frame_to_bars(frame(), get_calendar())
    a = market_context(bars, event(), AT+timedelta(minutes=10))
    changed = frame()
    changed.loc[changed.ts >= AT+timedelta(minutes=10), "close"] = 1000
    b = market_context(frame_to_bars(changed, get_calendar()), event(), AT+timedelta(minutes=10))
    assert a == b
    assert datetime.fromisoformat(a["last_bar_end"]) <= AT+timedelta(minutes=10)


def test_after_hours_waits_next_session():
    after = AT.replace(hour=22)
    e = event(published_at=after, content_at=after, received_at=after+timedelta(seconds=1))
    df = frame()
    df.ts = df.ts+timedelta(days=1)
    report = run(e, df)
    assert report["trades"][0]["entry"] == (AT+timedelta(days=1)).isoformat()


def test_split_crossing_purged():
    e = event()
    report = replay([e], {e.key: (decision(e), 1., "fixture", AT)}, frame(),
        train_end=AT+timedelta(minutes=3), validation_end=AT+timedelta(days=1), holding=(5,))
    assert not report["trades"]
    assert "split boundary" in report["exclusions"][0]["reason"]


def test_disabled_ai_and_paper_do_not_call_network(monkeypatch):
    monkeypatch.setenv("EVENT_AI_PROVIDER", "disabled")
    ai = AI()
    e = event()
    assert ai.evaluate(e, {})[0].side() == 0
    client = Mock()
    paper = PaperExecutor(client, EventStore(Storage(":memory:")))
    assert "disabled" in paper.enter(e, decision(e), {})
    client.assert_not_called()


def test_paper_endpoint_guard(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    client = Mock()
    with pytest.raises(RuntimeError):
        PaperExecutor(client, EventStore(Storage(":memory:"))).request("POST", "orders")
    client._request.assert_not_called()


@pytest.mark.parametrize("bias", ["bullish", "bearish"])
def test_paper_journal_is_reserved_before_submit(monkeypatch, bias):
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    now = datetime.now(UTC)
    e = event(mode="forward", published_at=now, content_at=now, received_at=now)
    store = EventStore(Storage(":memory:"))
    store.storage.put("event-paper-armed", True)
    client = Mock()
    client.get_paper_account.return_value = {"equity": "10000", "cash": "10000", "status": "ACTIVE"}
    paper = PaperExecutor(client, store)
    monkeypatch.setattr("botfolio.events.paper.get_calendar", lambda *a: Mock(session_for=lambda t:
        Mock(close=now+timedelta(hours=1))))
    paper._maintain = Mock(return_value=[])
    def request(method, path, **kwargs):
        if path == "clock":
            return {"is_open": True, "timestamp": now.isoformat()}
        if method == "POST":
            assert paper.intents()[0][1]["state"] == "reserved"
            body = kwargs["json"]
            assert body["order_class"] == "bracket"
            assert float(body["qty"])*float(body["limit_price"]) <= 2500
            stop, tp, limit = (float(body[k]["stop_price" if k == "stop_loss" else "limit_price"])
                               if k != "limit_price" else float(body[k]) for k in ("stop_loss", "take_profit", "limit_price"))
            sign = 1 if bias == "bullish" else -1
            assert body["side"] == ("buy" if sign == 1 else "sell") and stop*sign < limit*sign < tp*sign
            raise TimeoutError("ambiguous response")
        return []
    paper.request = request
    with pytest.raises(TimeoutError):
        paper.enter(e, decision(e).model_copy(update={"directional_hypothesis": bias, "recommended_action": "WAIT"}), {"t": now.isoformat(), "bp": 99.99, "ap": 100}, now)
    assert paper.intents()[0][1]["state"] == "reserved"


def test_chat_adapter_and_malformed_response_audit(monkeypatch):
    monkeypatch.setenv("EVENT_AI_PROVIDER", "chat")
    monkeypatch.setenv("EVENT_AI_MODEL", "fixture")
    monkeypatch.setenv("EVENT_AI_URL", "https://example.com/inference")
    monkeypatch.setenv("EVENT_AI_KEY", "test-only")
    e = event()
    response = Mock(status_code=200, text="fixture")
    response.json.return_value = {"choices": [{"message": {"content": decision(e).model_dump_json()}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr("botfolio.events.ai.requests.post", post)
    assert AI().evaluate(e, {})[0] == decision(e)
    assert post.call_args.kwargs["timeout"] == (10, 30.)
    response.json.return_value = {"invalid": "retained in audit"}
    store = EventStore(Storage(":memory:"))
    assert analyze(store, e, "bad-output", AI(), {})[0].side() == 0
    assert "retained in audit" in store.audit().response.iloc[0]


def test_systemone_adapter(monkeypatch):
    monkeypatch.setenv("EVENT_AI_PROVIDER", "systemone")
    monkeypatch.setenv("EVENT_AI_MODEL", "clef-flash")
    monkeypatch.setenv("EVENT_AI_URL", "https://example.com/inference")
    monkeypatch.setenv("EVENT_AI_KEY", "test-only")
    e = event()
    choices = {k: {"choice": v} for k, v in decision(e).model_dump().items()
               if k in {"fundamental_impact", "materiality", "expectations_surprise", "uncertainty",
                        "directional_hypothesis", "recommended_action"}}
    response = Mock(status_code=200, text="fixture")
    response.json.return_value = {"result": {"answers": choices}}
    monkeypatch.setattr("botfolio.events.ai.requests.post", Mock(return_value=response))
    d, request, raw = AI().evaluate(e, {})
    assert d.side() == 1
    assert len(request["questions"]) == 6
    assert d.confidence is None


def test_news_provider_pagination_and_revisions():
    from botfolio.events.providers import AlpacaNews
    client = Mock()
    article = dict(id=1, symbols=["SPY", "NVDA"], created_at=AT.isoformat(),
                   updated_at=(AT+timedelta(seconds=1)).isoformat(), source="fixture",
                   url="https://example.com/1", headline="Earnings", content="<p>Original</p>")
    client._request.side_effect = [{"news": [article], "next_page_token": "page2"}, {"news": []}]
    rows = AlpacaNews(client, ["SPY"]).fetch(AT, AT+timedelta(days=1))
    assert len(rows) == 1
    assert rows[0].ticker == "SPY"
    assert "Original" in rows[0].text
    assert rows[0].content_at > rows[0].published_at
    assert client._request.call_count == 2


def test_ml_purges_overlapping_training_labels(monkeypatch):
    import sys
    from types import SimpleNamespace

    import numpy as np

    from botfolio.events.ml import FEATURES, walk_forward
    class Model:
        def __init__(self, **kwargs):
            pass
        def fit(self, features, y):
            assert set(y) == {0, 1}
        def predict(self, features):
            return np.full(len(features), .6)
    monkeypatch.setitem(sys.modules, "lightgbm", SimpleNamespace(train=lambda *a, **kw: Model(), Dataset=lambda *a, **kw: None))
    data = pd.DataFrame([dict(event=str(i), entry=AT+timedelta(days=i),
        exit=AT+timedelta(days=i, hours=1), net_return=1 if i % 2 else -1,
        **{f: .1 for f in FEATURES}) for i in range(80)])
    output = walk_forward(data)
    assert output
    assert all(x["train_last_exit"] < x["test_first_entry"] for x in output)
    assert all(x["brier"] >= 0 for x in output)


def test_cli_import_and_report(tmp_path, capsys):
    from botfolio.cli import main
    path = tmp_path/"events.jsonl"
    path.write_text(event().model_dump_json()+"\n")
    db = str(tmp_path/"test.sqlite")
    assert main(["--db", db, "events", "import", str(path)]) == 0
    assert '"inserted": 1' in capsys.readouterr().out
    assert main(["--db", db, "events", "import", str(path)]) == 0
    assert '"inserted": 0' in capsys.readouterr().out
    assert main(["--db", db, "events", "report"]) == 0


def test_experiment_change_rejected():
    store = EventStore(Storage(":memory:"))
    store.freeze_experiment("fixed", {"model": "a"})
    with pytest.raises(ValueError, match="configuration changed"):
        store.freeze_experiment("fixed", {"model": "b"})


def test_optional_lightgbm_real_fit():
    pytest.importorskip("lightgbm")
    from botfolio.events.ml import FEATURES, walk_forward
    data = pd.DataFrame([dict(event=str(i), entry=AT+timedelta(days=i),
        exit=AT+timedelta(days=i, hours=1), net_return=1 if i % 2 else -1,
        **{f: i % 3 for f in FEATURES}) for i in range(80)])
    result = walk_forward(data)
    assert len(result) == 3
    assert all(0 <= p["probability"] <= 1 for fold in result for p in fold["predictions"])


def test_cli_replay_with_existing_technical_engine(tmp_path, monkeypatch, capsys):
    import json
    from datetime import date

    from botfolio.cli import main
    from botfolio.data.calendar import get_calendar
    from botfolio.data.synthetic import random_walk_bars
    start, end = date(2025, 1, 2), date(2025, 1, 10)
    df = random_walk_bars(get_calendar(), ["SPY"], start, end, seed=11, drop_prob=0)
    monkeypatch.setattr("botfolio.events.cli.load_history", lambda *a, **kw: df)
    monkeypatch.setenv("EVENT_AI_PROVIDER", "disabled")
    db = str(tmp_path/"test.sqlite")
    store = EventStore(Storage(db))
    store.add(event())
    output = tmp_path/"report.json"
    assert main(["--db", db, "events", "replay", "--start", "2025-01-02T00:00:00Z",
        "--end", "2025-01-11T00:00:00Z", "--train-end", "2025-01-03T23:59:59Z",
        "--validation-end", "2025-01-07T23:59:59Z", "--experiment", "disabled-test",
        "--run-id", "integration-test", "--cached", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["technical_comparison_run"] == "integration-test-technical"
    assert report["trades"] == []  # disabled AI abstains, never fabricates a directional edge
    assert store.storage.stats(report["technical_comparison_run"])


def test_forward_after_hours_pending_until_next_open():
    from botfolio.events.paper import event_is_fresh
    after = AT.replace(hour=22)
    e = event(mode="forward", published_at=after, content_at=after, received_at=after)
    next_open = AT+timedelta(days=1)
    assert event_is_fresh(e, next_open)
    assert event_is_fresh(e, next_open+timedelta(minutes=4))
    assert not event_is_fresh(e, next_open+timedelta(minutes=6))
