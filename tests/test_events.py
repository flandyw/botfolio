import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import ValidationError

from botfolio.events.ai import AI, abstain, analyze
from botfolio.events.context import market_context
from botfolio.events.models import Decision, Event
from botfolio.events.paper import PaperExecutor
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


def test_no_future_context():
    from botfolio.data.calendar import frame_to_bars, get_calendar
    bars = frame_to_bars(frame(), get_calendar())
    a = market_context(bars, event(), AT+timedelta(minutes=10))
    changed = frame()
    changed.loc[changed.ts >= AT+timedelta(minutes=10), "close"] = 1000
    b = market_context(frame_to_bars(changed, get_calendar()), event(), AT+timedelta(minutes=10))
    assert a == b
    assert datetime.fromisoformat(a["last_bar_end"]) <= AT+timedelta(minutes=10)


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
    blank = dict(article, id=2, headline="<br>", content=None, summary="")
    client._request.side_effect = [{"news": [blank], "next_page_token": "page2"}, {"news": [article]}]
    rows = AlpacaNews(client, ["SPY"]).fetch(AT, AT+timedelta(days=1))
    assert len(rows) == 1
    assert rows[0].ticker == "SPY"
    assert "Original" in rows[0].text
    assert rows[0].content_at > rows[0].published_at
    assert client._request.call_count == 2


def test_clairic_adapter_and_decision_stats(monkeypatch):
    monkeypatch.setenv("EVENT_AI_PROVIDER", "clairic")
    monkeypatch.setenv("EVENT_AI_MODEL", "clairic")
    monkeypatch.setenv("EVENT_AI_URL", "https://example.com/inference")
    monkeypatch.setenv("EVENT_AI_KEY", "test-only")
    e = event()
    expected = decision(e)
    enums = {name for name, field in expected.model_json_schema()["properties"].items() if "enum" in field}
    answers = {key: {"value": value, "valid": True, "complete": True}
               for key, value in expected.model_dump().items() if key in enums}
    answers["evidence"] = {"value": e.text}
    response = Mock()
    response.json.return_value = {"result": {"answers": answers, "usage": {"input_tokens": 123}}}
    post = Mock(return_value=response)
    monkeypatch.setattr("botfolio.events.ai.requests.post", post)
    store = EventStore(Storage(":memory:"))
    store.add(e)
    assert analyze(store, e, "clairic-v1", AI(), {})[0].side() == 1
    request = post.call_args.kwargs["json"]
    assert request["fields"]["recommended_action"]["type"] == "enum"
    assert isinstance(request["state"], str)
    answers["recommended_action"]["value"] = None
    assert analyze(store, e, "clairic-v2", AI(), {})[0].side() == 0
    stats = store.decision_stats().set_index("experiment")
    assert stats.loc["clairic-v1", "buy"] == 1
    assert stats.loc["clairic-v2", "wait"] == 1
    assert stats.loc["clairic-v1", "known_input_tokens"] == 123


def test_issuer_news_reads_rss_and_atom():
    from botfolio.events.providers import IssuerNews

    for xml in (
        "<rss><channel><item><title>Results</title><description>&lt;b&gt;Growth&lt;/b&gt;</description>"
        "<link>https://example.com/release</link><pubDate>Mon, 06 Jan 2025 14:30:00 GMT</pubDate>"
        "</item><item><pubDate>unknown</pubDate></item></channel></rss>",
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Results</title><summary>Growth</summary>'
        '<link href="https://example.com/release"/><published>2025-01-06T14:30:00Z</published>'
        '<updated>2025-01-06T14:31:00Z</updated></entry></feed>',
    ):
        provider = IssuerNews("AAPL", "https://example.com/feed")
        provider.http.get = Mock(return_value=Mock(content=xml.encode()))
        [row] = provider.fetch(AT - timedelta(days=1), AT + timedelta(days=1))
        assert row.text.split() == ["Results", "Growth"]
        assert row.source == "rss:example.com" and row.ticker == "AAPL"
        assert row.published_at <= row.content_at < row.received_at


def test_experiment_change_rejected():
    store = EventStore(Storage(":memory:"))
    store.freeze_experiment("fixed", {"model": "a"})
    with pytest.raises(ValueError, match="configuration changed"):
        store.freeze_experiment("fixed", {"model": "b"})


def test_forward_after_hours_pending_until_next_open():
    from botfolio.events.paper import event_is_fresh
    after = AT.replace(hour=22)
    e = event(mode="forward", published_at=after, content_at=after, received_at=after)
    next_open = AT+timedelta(days=1)
    assert event_is_fresh(e, next_open)
    assert event_is_fresh(e, next_open+timedelta(minutes=4))
    assert not event_is_fresh(e, next_open+timedelta(minutes=6))


def test_yahoo_news_round_robins_watchlist():
    from botfolio.events.providers import YahooNews

    provider = YahooNews(["AAPL", "BRK.B", "MSFT"], per_poll=2)
    provider.http.get = Mock(return_value=Mock(content=b"<rss><channel></channel></rss>"))
    provider.fetch(AT, AT)
    provider.fetch(AT, AT)
    urls = [c.args[0] for c in provider.http.get.call_args_list]
    assert [u.split("s=")[1].split("&")[0] for u in urls] == ["AAPL", "BRK-B", "MSFT", "AAPL"]


def router_env(monkeypatch, share=None):
    from botfolio.events.ai import Router

    monkeypatch.setenv("EVENT_AI_PROVIDER", "clairic")
    monkeypatch.setenv("EVENT_AI_MODEL", "clairic")
    monkeypatch.setenv("EVENT_AI_URL", "https://cf.example/ai/run/@cf/cloudflare/clairic")
    monkeypatch.setenv("EVENT_AI_KEY", "k")
    monkeypatch.setenv("EVENT_AI_SECONDARY_PROVIDER", "chat")
    monkeypatch.setenv("EVENT_AI_SECONDARY_MODEL", "meta/muse-ember")
    monkeypatch.setenv("EVENT_AI_SECONDARY_URL", "https://vercel.example/v1/chat/completions")
    monkeypatch.setenv("EVENT_AI_SECONDARY_KEY", "k")
    monkeypatch.setenv("EVENT_AI_SECONDARY_EXPERIMENT", "muse-v1")
    monkeypatch.setenv("EVENT_AI_MONTHLY_USD", "5")
    if share is None:
        monkeypatch.delenv("EVENT_AI_SECONDARY_SHARE", raising=False)
    else:
        monkeypatch.setenv("EVENT_AI_SECONDARY_SHARE", share)
    return Router("clairic-v2")


def spent(store, model, response, cost=None):
    with store.storage.tx() as conn:
        conn.execute("INSERT INTO event_decisions VALUES (?,?,?,?,?,?,?,?,?)", (
            f"{model}{cost}{json.dumps(response)}", "x", model, "{}", "{}", json.dumps(response), 1.0, cost, datetime.now(UTC).isoformat()))


def test_router_fixed_share_splits_articles(monkeypatch):
    router, store = router_env(monkeypatch, "0.4"), EventStore(Storage(":memory:"))
    picks = [router.use_secondary(store, event(source_id=str(i)), now=i * 60.0) for i in range(500)]
    assert 150 < sum(picks) < 250
    same = event(source_id="7")
    assert router.use_secondary(store, same, now=1e6) == router.use_secondary(store, same, now=2e6)


def test_router_paces_budget_and_overflows_free_neurons(monkeypatch):
    router, store = router_env(monkeypatch), EventStore(Storage(":memory:"))
    assert router.share == "auto" and router.use_secondary(store, event(), now=0)
    spent(store, "chat:meta/muse-ember", {}, cost=1.0)  # far over today's pace
    assert not router.use_secondary(store, event(), now=100)
    spent(store, "clairic:clairic", {"result": {"usage": {"input_tokens": 2_000_000}}})  # past the free neurons
    assert router.use_secondary(store, event(), now=200)
    router.configure({"monthly_usd": 20})  # a bigger budget from the control page reopens Vercel
    assert router.use_secondary(store, event(), now=300)
    spent(store, "chat:meta/muse-ember", {}, cost=19.5)  # the month's credit is gone
    assert not router.use_secondary(store, event(), now=400)


def test_router_caps_rate_and_falls_back_when_throttled(monkeypatch):
    from botfolio.events.ai import Throttled

    router, store = router_env(monkeypatch), EventStore(Storage(":memory:"))
    router.calls = [0.0] * router.rpm
    assert not router.use_secondary(store, event(), now=1)
    assert router.use_secondary(store, event(), now=61)
    router.calls = []
    router.secondary.evaluate = Mock(side_effect=Throttled(429, 120))
    router.primary.evaluate = Mock(side_effect=lambda e, c: (abstain(e, "fallback"), {}, {}))
    decision = router.analyze(store, event(), {})
    assert decision[2] == "clairic:clairic" and store.storage.get("ai-throttled")["status"] == 429
    assert not router.use_secondary(store, event(source_id="2"))
    assert router.decision(store, event()) is not None


def test_router_applies_saved_models(monkeypatch):
    from botfolio.events.ai import Router

    monkeypatch.setenv("EVENT_AI_PROVIDER", "clairic")
    monkeypatch.setenv("EVENT_AI_MODEL", "clairic")
    monkeypatch.setenv("EVENT_AI_URL", "https://cf.example/ai/run/@cf/cloudflare/clairic")
    monkeypatch.setenv("EVENT_AI_KEY", "k")
    monkeypatch.setenv("EVENT_AI_SECONDARY_PROVIDER", "chat")
    monkeypatch.setenv("EVENT_AI_SECONDARY_MODEL", "meta/muse-ember")
    monkeypatch.setenv("EVENT_AI_SECONDARY_URL", "https://vercel.example/v1/chat/completions")
    monkeypatch.setenv("EVENT_AI_SECONDARY_KEY", "k")
    monkeypatch.setenv("EVENT_AI_SECONDARY_EXPERIMENT", "muse-v1")
    router = Router("clairic-v2")
    router.configure({"primary_model": "clairic-flash", "secondary_model": "meta/muse-glimmer-30b", "share": 1})
    assert router.primary.url.endswith("/@cf/cloudflare/clairic-flash")
    assert router.primary_experiment == "clairic-v2+clairic-flash"
    assert router.secondary_experiment == "muse-v1+meta-muse-glimmer-30b" and router.share == 1
    monkeypatch.delenv("EVENT_AI_SECONDARY_SHARE", raising=False)
    router.configure({})
    assert (router.primary_experiment, router.share) == ("clairic-v2", "auto")
