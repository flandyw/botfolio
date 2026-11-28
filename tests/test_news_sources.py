import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import requests

from botfolio.events.activity import news_performance
from botfolio.events.ai import CHAT_INSTRUCTIONS, CHAT_REVISION, Router
from botfolio.events.context import trade_terms
from botfolio.events.models import Event
from botfolio.events.paper import NEWS_STOP
from botfolio.events.providers import PRNewswire, SECCurrent
from botfolio.events.store import EventStore
from botfolio.storage import Storage
from tests.test_activity import Broker, order

AT = datetime(2025, 1, 6, 14, 45, tzinfo=UTC)


def test_prnewswire_keeps_only_releases_naming_a_watchlist_listing():
    xml = ("<rss><channel>"
           "<item><title>NVIDIA and AMD partner</title><description>NVIDIA (NASDAQ: NVDA) and AMD (Nasdaq: AMD) today"
           "</description><link>https://www.prnewswire.com/a.html</link><pubDate>Mon, 06 Jan 2025 14:30:00 GMT</pubDate></item>"
           "<item><title>Reward offered</title><description>NVDA fans, no listing</description>"
           "<link>https://www.prnewswire.com/b.html</link><pubDate>Mon, 06 Jan 2025 14:30:00 GMT</pubDate></item>"
           "<item><title>Miner update</title><description>Miner (NYSE American: MTA) reports</description>"
           "<link>https://www.prnewswire.com/c.html</link><pubDate>Mon, 06 Jan 2025 14:30:00 GMT</pubDate></item>"
           "</channel></rss>")
    provider = PRNewswire(["NVDA", "AMD", "AAPL"], Mock(get=Mock(return_value=Mock(content=xml.encode()))))
    rows = provider.fetch(AT - timedelta(days=1), AT + timedelta(days=1))
    assert [(r.ticker, r.source, r.source_id) for r in rows] == [
        ("AMD", "rss:www.prnewswire.com", "https://www.prnewswire.com/a.html"),
        ("NVDA", "rss:www.prnewswire.com", "https://www.prnewswire.com/a.html")]


def test_sec_current_maps_ciks_to_watchlist_and_fetches_each_filing_once():
    def entry(form, cik, accession):
        return (f"<entry><title>{form} - Company ({cik:010d}) (Filer)</title>"
                f'<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/'
                f'{accession}-index.htm"/><summary type="html">Item 2.02: Results of Operations</summary>'
                "<updated>2025-01-06T09:40:00-05:00</updated></entry>")
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom">' + entry("8-K", 320193, "0000320193-25-000001")
            + entry("8-K", 999, "0000000999-25-000001") + entry("8-K/A", 789019, "0000789019-25-000001")
            + entry("8-K", 1045810, "0001045810-25-000001") + "</feed>")
    tickers = {"0": dict(cik_str=320193, ticker="AAPL"), "1": dict(cik_str=789019, ticker="MSFT"),
               "2": dict(cik_str=1045810, ticker="NVDA"), "3": dict(cik_str=1045810, ticker="NV-B")}
    calls, down = [], [True]

    def get(url):
        calls.append(url)
        if "1045810" in url and down:
            raise RuntimeError("provider retries exhausted") if len(calls) < 5 else requests.ConnectionError("reset")
        if url == SECCurrent.TICKERS:
            return Mock(json=lambda: tickers)
        if url == SECCurrent.FEED:
            return Mock(content=feed.encode())
        return Mock(text="x" * 300_000 if "1045810" in url else "<p>Apple raised guidance</p>")

    provider = SECCurrent(["AAPL", "MSFT", "NV.B"], "Botfolio test@example.com", Mock(get=get))
    rows = provider.fetch(AT - timedelta(days=1), AT + timedelta(days=1))
    assert [r.ticker for r in rows] == ["AAPL"]  # a failed download keeps the filings already read
    assert provider.fetch(AT - timedelta(days=1), AT + timedelta(days=1)) == []  # a connection error does too
    down.clear()
    rows += provider.fetch(AT - timedelta(days=1), AT + timedelta(days=1))
    assert [(r.ticker, r.source, r.source_id, r.text) for r in rows] == [
        ("AAPL", "sec", "0000320193-25-000001", "Apple raised guidance"),
        ("NV.B", "sec", "0001045810-25-000001", "8-K - Company (0001045810) (Filer) Item 2.02: Results of Operations")]
    assert rows[0].source_url.endswith("/000032019325000001/0000320193-25-000001.txt")
    assert provider.fetch(AT - timedelta(days=1), AT + timedelta(days=1)) == []
    assert calls.count(SECCurrent.TICKERS) == 1 and len(calls) == 9  # tickers, 4 feeds, 4 filing requests


def test_prompt_states_the_executed_trade(monkeypatch):
    terms = trade_terms(15)
    assert f"{NEWS_STOP:.0%} stop" in terms and "no profit target" in terms and "after 15 minutes" in terms
    assert "1% stop" not in CHAT_INSTRUCTIONS
    monkeypatch.setenv("EVENT_AI_VERCEL_MODEL", "meta/muse-ember")
    monkeypatch.setenv("EVENT_AI_VERCEL_URL", "https://vercel.example/v1/chat/completions")
    monkeypatch.setenv("EVENT_AI_VERCEL_KEY", "k")
    router = Router("clairic-v1")
    assert router.experiment["cloudflare"] == "clairic-v1"
    assert router.experiment["vercel"] == "vercel-v1" + CHAT_REVISION
    assert all(router.experiment[name].endswith(CHAT_REVISION) == (ai.provider in ("chat", "chatgpt", "claude"))
               for name, ai in router.ai.items())


def test_news_performance_groups_fills_by_source_and_model():
    store = EventStore(Storage(":memory:"))
    rows = []
    for ticker, source, model in (("X", "sec", "claude:opus"), ("Y", "alpaca:benzinga", "chat:gpt")):
        e = Event(source=source, source_id=ticker, ticker=ticker, event_type="news", published_at=AT, content_at=AT,
                  received_at=AT + timedelta(seconds=1), source_url="https://example.com/" + ticker, text="Guidance raised")
        store.add(e)
        with store.storage.tx() as conn:
            conn.executemany("INSERT INTO event_decisions VALUES (?,?,?,?,?,?,?,?,?)", [
                (e.key, "old", "chat:abstained", '{"recommended_action": "WAIT"}', "{}", "{}", 0.1, None, AT.isoformat()),
                (e.key, "exp", model, '{"recommended_action": "BUY"}', "{}", "{}", 0.1, None,
                 (AT + timedelta(minutes=1)).isoformat())])
        rows.append(dict(key=e.key, client_id="evt-" + ticker, symbol=ticker, side="long", opened=AT.isoformat()))
    rows.append(dict(key="m", client_id="evt-m", strategy="momentum", symbol="Q", side="long", opened=AT.isoformat()))
    broker = Broker([order("evt-X", 100, 10, [order("target", 103, 10)]), order("evt-Y", 50, 4, [order("stop", 49, 4)]),
                     order("evt-m", 10, 1)], [])
    broker.request = Mock(side_effect=broker.request)
    result = news_performance(broker, rows, store)
    assert broker.request.call_count == 2  # one orders page and one positions read for both tables
    assert {k: v["realized"] for k, v in result["sources"].items()} == {"sec": 30, "alpaca:benzinga": -4}
    assert {k: v["realized"] for k, v in result["models"].items()} == {"claude:opus": 30, "chat:gpt": -4}
    assert json.dumps(result)


def test_sec_current_leaves_companies_followed_by_sec_filings():
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>8-K - Apple (0000320193) (Filer)</title>'
            '<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/'
            '0000320193-25-000001-index.htm"/><updated>2025-01-06T09:40:00-05:00</updated></entry></feed>')

    def source(text):
        return Mock(get=lambda url: Mock(json=lambda: {"0": dict(cik_str=320193, ticker="AAPL")}, content=feed.encode(), text=text))

    window = AT - timedelta(days=1), AT + timedelta(days=1)
    followed = {"AAPL": "320193"}
    assert SECCurrent(["AAPL"], "Botfolio test@example.com", source("filing"), exclude=followed).fetch(*window) == []
    assert len(SECCurrent(["AAPL"], "Botfolio test@example.com", source("filing")).fetch(*window)) == 1
    # SECFilings skips a submission this large, so it is reported here even for a followed company.
    big = SECCurrent(["AAPL"], "Botfolio test@example.com", source("<p>x</p>" * 30_000), exclude=followed).fetch(*window)
    assert [e.ticker for e in big] == ["AAPL"]
