import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import ValidationError

from botfolio.events.ai import AI, Throttled, abstain, analyze
from botfolio.events.context import market_context
from botfolio.events.models import Decision, Event
from botfolio.events.paper import PaperExecutor
from botfolio.events.store import EventStore
from botfolio.storage import Storage

AT = datetime(2025, 1, 6, 14, 45, tzinfo=UTC)  # 09:45 ET, after the opening wait


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
    assert decision(e).model_copy(update={"recommended_action": "HOLD"}).side() == 0


def test_since_prev_close():
    from botfolio.events.context import since_prev_close
    def bars(price, today, prev):
        return {"latestTrade": {"p": price, "t": "2026-11-25T13:19:26.123456789Z"},  # 08:19 ET, pre-market
                "dailyBar": {"t": "2026-11-24T05:00:00Z", "c": today}, "prevDailyBar": {"t": "2026-11-23T05:00:00Z", "c": prev}}
    snap = {"NVDA": bars(95, 100, 120), "SPY": bars(198, 200, 300)}
    move = since_prev_close(snap, "NVDA")
    assert round(move["since_prev_close"], 4) == -.05 and round(move["market_since_prev_close"], 4) == -.01
    snap["NVDA"]["dailyBar"]["t"] = "2026-11-25T05:00:00Z"  # mid-session: today's bar is still forming
    snap["NVDA"]["latestTrade"]["t"] = "2026-11-25T15:00:00Z"
    assert round(since_prev_close(snap, "NVDA")["since_prev_close"], 4) == round(95/120-1, 4)
    assert since_prev_close({}, "NVDA")["since_prev_close"] is None


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


@pytest.mark.parametrize("cash", ["10000", "-2000"])  # negative: cash parked in BOXX must not block entries
@pytest.mark.parametrize("bias", ["bullish", "bearish"])
def test_paper_journal_is_reserved_before_submit(monkeypatch, bias, cash):
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    now = datetime.now(UTC)
    e = event(mode="forward", published_at=now, content_at=now, received_at=now)
    store = EventStore(Storage(":memory:"))
    store.storage.put("event-paper-armed", True)
    client = Mock()
    client.get_paper_account.return_value = {"equity": "10000", "cash": cash, "buying_power": "20000",
                                             "status": "ACTIVE"}
    paper = PaperExecutor(client, store)
    monkeypatch.setattr("botfolio.events.paper.get_calendar", lambda *a: Mock(session_for=lambda t:
        Mock(open=now-timedelta(hours=1), close=now+timedelta(hours=1))))
    paper._maintain = Mock(return_value=[])
    def request(method, path, **kwargs):
        if path == "clock":
            return {"is_open": True, "timestamp": now.isoformat()}
        if method == "POST":
            assert paper.intents()[0][1]["state"] == "reserved"
            body = kwargs["json"]
            assert body["order_class"] == "oto" and "take_profit" not in body
            assert float(body["qty"])*float(body["limit_price"]) <= 2500
            stop, limit = float(body["stop_loss"]["stop_price"]), float(body["limit_price"])
            sign = 1 if bias == "bullish" else -1
            assert body["side"] == ("buy" if sign == 1 else "sell")
            assert limit == (100.4 if sign == 1 else 99.59) and stop == round(limit*(1-sign*.02), 2)
            raise TimeoutError("ambiguous response")
        return []
    paper.request = request
    quote = {"t": now.isoformat(), "bp": 99.99, "ap": 100}
    assert paper.enter(e, decision(e).model_copy(update={"materiality": "medium"}), quote, now) == "abstain: not high materiality"
    yahoo = event(mode="forward", published_at=now, content_at=now, received_at=now, source="rss:feeds.finance.yahoo.com")
    assert "Yahoo" in paper.enter(yahoo, decision(yahoo), quote, now)
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
    fenced = "```json\n" + decision(e).model_dump_json() + "\n```"  # how free OpenRouter models often answer
    response.json.return_value = {"choices": [{"message": {"content": fenced}}]}
    assert AI().evaluate(e, {})[0] == decision(e)
    response.json.return_value = {"invalid": "retained in audit"}
    store = EventStore(Storage(":memory:"))
    assert analyze(store, e, "bad-output", AI(), {})[0].side() == 0
    assert "retained in audit" in store.audit().response.iloc[0]


def test_chatgpt_sidecar_adapter(monkeypatch):
    monkeypatch.setenv("EVENT_AI_PROVIDER", "chatgpt")
    monkeypatch.setenv("EVENT_AI_MODEL", "gpt-fixture")
    monkeypatch.setenv("EVENT_AI_URL", "https://example.com/respond")
    with pytest.raises(ValueError):  # only the loopback sidecar holds ChatGPT credentials
        AI()
    monkeypatch.setenv("EVENT_AI_URL", "http://127.0.0.1:8611/respond")
    e = event()
    response = Mock(status_code=200, text="fixture")
    response.json.return_value = {"text": decision(e).model_dump_json()}
    post = Mock(return_value=response)
    monkeypatch.setattr("botfolio.events.ai.requests.post", post)
    assert AI().evaluate(e, {})[0] == decision(e)
    assert set(post.call_args.kwargs["json"]) == {"model", "input", "instructions"}
    restated = decision(e).model_dump(mode="json") | {"ticker": "nvda", "event_timestamp": "2026-01-01T00:00:00Z"}
    response.json.return_value = {"text": json.dumps(restated)}
    assert AI().evaluate(e, {})[0] == decision(e)  # identity comes from the event, not the model's restatement
    response.status_code, response.headers = 429, {}
    with pytest.raises(Throttled):
        AI().evaluate(e, {})


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
    insecure = dict(article, id=3, url="http://example.com/3")
    client._request.side_effect = [{"news": [blank, insecure], "next_page_token": "page2"}, {"news": [article]}]
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


def test_news_outside_regular_hours_waits_for_the_open():
    from botfolio.events.paper import event_is_fresh
    regular_open = AT.replace(day=7, hour=14, minute=30)  # 09:30 ET
    for at in (AT.replace(hour=22), AT.replace(day=7, hour=11), AT.replace(day=7, hour=13, minute=10)):  # 17:00, 06:00, 08:10 ET
        e = event(mode="forward", published_at=at, content_at=at, received_at=at)
        assert event_is_fresh(e, regular_open+timedelta(minutes=4))
        assert not event_is_fresh(e, regular_open+timedelta(minutes=6))


def test_issuer_rss_tolerates_ten_minute_feed_lag():
    from botfolio.events.paper import event_is_fresh
    lagged = dict(mode="forward", published_at=AT, content_at=AT, received_at=AT+timedelta(minutes=7))
    assert event_is_fresh(event(**lagged, source="rss:nvidianews.nvidia.com"), AT+timedelta(minutes=8))
    assert not event_is_fresh(event(**lagged, source="rss:feeds.finance.yahoo.com"), AT+timedelta(minutes=8))


def test_yahoo_news_round_robins_watchlist():
    from botfolio.events.providers import YahooNews

    provider = YahooNews(["AAPL", "BRK.B", "MSFT"], per_poll=2)
    provider.http.get = Mock(return_value=Mock(content=b"<rss><channel></channel></rss>"))
    provider.fetch(AT, AT)
    provider.fetch(AT, AT)
    urls = [c.args[0] for c in provider.http.get.call_args_list]
    assert [u.split("s=")[1].split("&")[0] for u in urls] == ["AAPL", "BRK-B", "MSFT", "AAPL"]


def router_env(monkeypatch, order=("vercel", "cloudflare")):
    from botfolio.events.ai import Router

    for name in ("CLAUDE", "CHATGPT"):
        monkeypatch.delenv(f"EVENT_AI_{name}_MODEL", raising=False)
    monkeypatch.setenv("EVENT_AI_PROVIDER", "clairic")
    monkeypatch.setenv("EVENT_AI_MODEL", "clairic")
    monkeypatch.setenv("EVENT_AI_URL", "https://cf.example/ai/run/@cf/cloudflare/clairic")
    monkeypatch.setenv("EVENT_AI_KEY", "k")
    monkeypatch.setenv("EVENT_AI_VERCEL_MODEL", "meta/muse-ember")
    monkeypatch.setenv("EVENT_AI_VERCEL_URL", "https://vercel.example/v1/chat/completions")
    monkeypatch.setenv("EVENT_AI_VERCEL_KEY", "k")
    monkeypatch.setenv("EVENT_AI_VERCEL_EXPERIMENT", "muse-v1")
    monkeypatch.setenv("EVENT_AI_MONTHLY_USD", "5")
    router = Router("clairic-v2")
    router.configure({"order": list(order)})
    return router


def spent(store, model, response, cost=None):
    with store.storage.tx() as conn:
        conn.execute("INSERT INTO event_decisions VALUES (?,?,?,?,?,?,?,?,?)", (
            f"{model}{cost}{json.dumps(response)}", "x", model, "{}", "{}", json.dumps(response), 1.0, cost, datetime.now(UTC).isoformat()))


def test_router_paces_budget_and_overflows_free_neurons(monkeypatch):
    router, store = router_env(monkeypatch), EventStore(Storage(":memory:"))
    assert router.ready("vercel", store, now=0)
    spent(store, "chat:meta/muse-ember", {}, cost=1.0)  # far over today's pace
    assert not router.ready("vercel", store, now=100)
    spent(store, "clairic:clairic", {"result": {"usage": {"input_tokens": 2_000_000}}})  # past the free neurons
    assert router.ready("vercel", store, now=200)
    router.configure({"order": router.order, "monthly_usd": 20})  # a bigger budget from the control page
    assert router.ready("vercel", store, now=300)
    spent(store, "chat:meta/muse-ember", {}, cost=19.5)  # the month's credit is gone
    assert not router.ready("vercel", store, now=400) and router.ready("cloudflare", store, now=400)


def test_router_caps_rate_and_falls_back_when_throttled(monkeypatch):
    router, store = router_env(monkeypatch), EventStore(Storage(":memory:"))
    router.calls = [0.0] * router.rpm
    assert not router.ready("vercel", store, now=1)
    assert router.ready("vercel", store, now=61)
    router.calls = []
    router.ai["vercel"].evaluate = Mock(side_effect=Throttled(429, 120))
    router.ai["cloudflare"].evaluate = Mock(side_effect=lambda e, c: (abstain(e, "fallback"), {}, {}))
    decision = router.analyze(store, event(), {})
    assert decision[2] == "clairic:clairic" and store.storage.get("ai-throttled")["vercel"]["status"] == 429
    assert not router.ready("vercel", store)
    assert router.decision(store, event()) is not None


def test_router_follows_priority_order(monkeypatch):
    router, store = router_env(monkeypatch, order=("cloudflare", "vercel")), EventStore(Storage(":memory:"))
    for ai in router.ai.values():
        ai.evaluate = Mock(side_effect=lambda e, c: (abstain(e, "x"), {}, {}))
    assert router.analyze(store, event(), {})[2] == "clairic:clairic"
    router.configure({"order": ["vercel"]})
    for ai in router.ai.values():
        ai.evaluate = Mock(side_effect=lambda e, c: (abstain(e, "x"), {}, {}))
    assert router.analyze(store, event(source_id="2"), {})[2] == "chat:meta/muse-ember"
    spent(store, "chat:meta/muse-ember", {}, cost=19.5)  # Vercel spent out and Cloudflare left out of the order
    assert router.analyze(store, event(source_id="3"), {})[2] == "none:none"
    assert router.decision(store, event(source_id="3"))[0].recommended_action == "WAIT"  # not asked again


def test_router_applies_saved_models(monkeypatch):
    router = router_env(monkeypatch)
    router.configure({"models": {"cloudflare": "clairic-flash", "vercel": "meta/muse-glimmer-30b"}})
    assert router.ai["cloudflare"].url.endswith("/@cf/cloudflare/clairic-flash")
    assert router.experiment["cloudflare"] == "clairic-v2+clairic-flash"
    assert router.experiment["vercel"] == "muse-v1+p2+meta-muse-glimmer-30b"
    assert router.order == ["claude", "chatgpt", "vercel", "cloudflare"]  # the default priority
    router.configure({})
    assert router.experiment["cloudflare"] == "clairic-v2"


def test_plans_use_call_caps_not_the_spend_budget(monkeypatch):
    router, store = router_env(monkeypatch), EventStore(Storage(":memory:"))
    monkeypatch.setenv("EVENT_AI_CHATGPT_MODEL", "gpt-6.2-sol")
    monkeypatch.setenv("EVENT_AI_CHATGPT_URL", "http://127.0.0.1:8611/respond")
    monkeypatch.setenv("EVENT_AI_CLAUDE_MODEL", "sonnet")
    router.configure({"order": ["claude", "chatgpt", "cloudflare"]})
    spent(store, "chat:meta/muse-ember", {}, cost=19.5)  # paid gateway budget long gone
    assert router.ready("chatgpt", store) and not router.ready("vercel", store)
    monkeypatch.setenv("EVENT_AI_CHATGPT_5H_CALLS", "1")
    spent(store, "chatgpt:gpt-6.2-sol", {})
    assert not router.ready("chatgpt", store)  # the bot's own 5-hour cap
    monkeypatch.setenv("EVENT_AI_CHATGPT_5H_CALLS", "100")
    assert router.ready("chatgpt", store)
    router.cooldown["chatgpt"] = float("inf")  # after a 429 from the plan's sharing limit
    assert not router.ready("chatgpt", store) and router.ready("claude", store)


def test_claude_cli_call_and_limits(monkeypatch):
    import subprocess

    monkeypatch.setenv("EVENT_AI_CLAUDE_MODEL", "sonnet")
    monkeypatch.setenv("EVENT_AI_CLAUDE_BIN", "/bin/claude")
    ai, e = AI("EVENT_AI_CLAUDE", "claude"), event()
    answer = {**json.loads(decision(e).model_dump_json()), "ticker": "WRONG"}
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({"is_error": False, "structured_output": answer}), ""))
    monkeypatch.setattr(subprocess, "run", run)
    got, request, _ = ai.evaluate(e, {})
    assert got.ticker == e.ticker and json.loads(request["input"])["event"]["ticker"] == e.ticker
    command = run.call_args.args[0]
    assert command[:4] == ["/bin/claude", "-p", "--model", "sonnet"] and "--json-schema" in command
    assert command[command.index("--tools") + 1] == ""  # no tools: the article text cannot make it act
    run.return_value = subprocess.CompletedProcess([], 1, json.dumps(
        {"is_error": True, "api_error_status": 429, "result": "Claude AI usage limit reached"}), "")
    with pytest.raises(Throttled):
        ai.evaluate(e, {})


def test_news_context_is_point_in_time():
    from botfolio.events.context import news_context

    e = event(mode="forward", published_at=AT, content_at=AT, received_at=AT)
    earlier = [event(source_id=str(i), text=f"headline {i}", published_at=AT - timedelta(hours=h),
                     content_at=AT - timedelta(hours=h), received_at=AT - timedelta(hours=h))
               for i, h in ((1, 1), (2, 30))]
    later = event(source_id="9", text="later", received_at=AT + timedelta(minutes=1), published_at=AT, content_at=AT)
    other = event(source_id="8", ticker="MSFT", text="other", published_at=AT - timedelta(hours=1),
                  content_at=AT - timedelta(hours=1), received_at=AT - timedelta(hours=1))
    ctx = news_context(e, [e, *earlier, later, other], "Apple Inc.")
    assert ctx["company"] == "Apple Inc." and ctx["session"].startswith("regular session, 15 min after the open")
    assert [h["headline"] for h in ctx["recent_headlines"]] == ["headline 1"]  # not itself, older or later news
