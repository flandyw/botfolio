"""Polling providers. Archive actual responses as immutable event revisions."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests

from botfolio.data.alpaca import AlpacaClient
from botfolio.events.models import Event

ISSUER_FEEDS = {
    "AAPL": "https://www.apple.com/newsroom/rss-feed.rss",
    "NVDA": "https://nvidianews.nvidia.com/cats/press_release.xml",
    "GOOGL": "https://blog.google/rss/",
    "META": "https://about.fb.com/feed/",
    "AMZN": "https://www.aboutamazon.com/news/rss",
}
PRNEWSWIRE_FEED = "https://www.prnewswire.com/rss/news-releases-list.rss"
YAHOO_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={}&region=US&lang=en-US"
BROWSER = {"User-Agent": "Mozilla/5.0"}  # Yahoo answers 429 to the default requests agent


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(html):
    parser = Text()
    parser.feed(html)
    return " ".join(parser.parts)


class HTTP:
    """Single sequential provider worker: bounded timeouts, retries, rate and short-lived cache."""
    def __init__(self, headers=None, interval=0.25):
        self.session = requests.Session()
        self.session.headers.update(headers or {})
        self.interval, self.last = interval, 0.0
        self.cache = {}

    def get(self, url):
        cached = self.cache.get(url)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        for attempt in range(4):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                response = self.session.get(url, timeout=(10, 30))
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                response.raise_for_status()
                self.cache[url] = (time.monotonic(), response)
                return response
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("provider retries exhausted")


class AlpacaNews:
    def __init__(self, client: AlpacaClient, symbols: list[str]):
        self.client, self.symbols = client, symbols

    async def poll(self, start, end):
        return await asyncio.to_thread(self.fetch, start, end)

    def fetch(self, start, end):
        params = dict(symbols=",".join(self.symbols), start=start.isoformat(), end=end.isoformat(),
                      include_content="true", limit=50, sort="asc")
        result = []
        while True:
            data = self.client._request("GET", "https://data.alpaca.markets/v1beta1/news", params=params)
            received = datetime.now(UTC)
            for article in data.get("news", []):
                text = plain(" ".join(article.get(field) or "" for field in ("headline", "content", "summary")))
                if not text.strip() or not (article.get("url") or "").startswith("https://"):
                    continue  # one http:// link failed the whole poll and blocked news entries
                for ticker in sorted(set(article["symbols"]) & set(self.symbols)):
                    result.append(Event(source="alpaca:" + article.get("source", "unknown"),
                        source_id=str(article["id"]), ticker=ticker, event_type="news",
                        published_at=article["created_at"], content_at=article["updated_at"],
                        received_at=received, source_url=article["url"],
                        text=text))
            token = data.get("next_page_token")
            if not token:
                return result
            params["page_token"] = token
            time.sleep(0.35)


class IssuerNews:
    def __init__(self, ticker, url, http=None):
        self.ticker, self.url = ticker, url
        self.http = http or HTTP(BROWSER)

    async def poll(self, start, end):
        return await asyncio.to_thread(self.fetch, start, end)

    def fetch(self, start, end):
        root = ElementTree.fromstring(self.http.get(self.url).content)
        received = datetime.now(UTC)
        atom = "{http://www.w3.org/2005/Atom}"
        result = []
        for item in root.findall("./channel/item") + root.findall(atom + "entry"):
            if item.tag == "item":
                parse_date = parsedate_to_datetime
                published_at = updated_at = item.findtext("pubDate", "")
                link = item.findtext("link", "").strip()
                headline, description = item.findtext("title", ""), item.findtext("description", "")
            else:
                parse_date = datetime.fromisoformat
                updated_at = item.findtext(atom + "updated", "")
                published_at = item.findtext(atom + "published") or updated_at
                link = next((tag.get("href", "") for tag in item.findall(atom + "link")
                             if tag.get("rel", "alternate") == "alternate"), "")
                headline = item.findtext(atom + "title", "")
                description = item.findtext(atom + "summary", "")
            try:
                published = parse_date(published_at)
                updated = parse_date(updated_at or published_at)
            except ValueError:
                continue
            text = plain(f"{headline} {description}").strip()
            if (published.utcoffset() is None or updated.utcoffset() is None
                or not published <= updated <= received or not start <= updated <= end
                or not text or len(text) > 200_000 or not link.startswith("https://")):
                continue
            result += [Event(source="rss:" + urlsplit(self.url).netloc, source_id=link,
                ticker=ticker, event_type="news", published_at=published, content_at=updated,
                received_at=received, source_url=link, text=text) for ticker in self.tickers(text)]
        return result

    def tickers(self, text):
        return [self.ticker]


class PRNewswire(IssuerNews):
    """Company press releases straight from the wire. A release belongs to the watchlist symbols it names
    with an exchange, e.g. "(NASDAQ: NVDA)"; releases naming none are dropped."""
    LISTING = re.compile(r"\((?:NYSE(?: American| Arca)?|NASDAQ|Nasdaq(?:GS|GM|CM)?|CBOE|Cboe)\s*:\s*([A-Z][A-Z.]{0,5})\)")

    def __init__(self, symbols, http=None):
        super().__init__(None, PRNEWSWIRE_FEED, http)
        self.symbols = symbols

    def tickers(self, text):
        return sorted(set(self.LISTING.findall(text)) & set(self.symbols))


class YahooNews:
    """Yahoo Finance headlines for the whole watchlist, a few tickers per poll so a cycle never waits on all."""
    def __init__(self, symbols, per_poll=3):
        self.symbols, self.per_poll, self.next = symbols, per_poll, 0
        self.http = HTTP(BROWSER)

    async def poll(self, start, end):
        return await asyncio.to_thread(self.fetch, start, end)

    def fetch(self, start, end):
        if not self.symbols:
            return []
        batch = [self.symbols[(self.next + i) % len(self.symbols)] for i in range(min(self.per_poll, len(self.symbols)))]
        self.next = (self.next + len(batch)) % len(self.symbols)
        return [e for s in batch for e in IssuerNews(s, YAHOO_FEED.format(s.replace(".", "-")), self.http).fetch(start, end)]


class SECFilings:
    def __init__(self, companies: dict[str, str], user_agent: str):
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must identify your organization and contact email")
        self.companies = companies
        self.http = HTTP({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, interval=0.25)

    async def poll(self, start, end):
        return await asyncio.to_thread(self.fetch, start, end)

    def fetch(self, start, end):
        result = []
        for ticker, cik in self.companies.items():
            cik = str(int(cik)).zfill(10)
            recent = self.http.get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()["filings"]["recent"]
            for i, form in enumerate(recent["form"]):
                if form not in {"8-K", "10-Q", "10-K"}:
                    continue
                published = datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00"))
                if not start <= published <= end:
                    continue
                accession = recent["accessionNumber"][i]
                # Complete submission includes exhibits; never replace with current companyfacts aggregates.
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}.txt"
                content = self.http.get(url).text
                if len(content) > 200000:
                    # shortcut: oversized filings are skipped, add section-aware extraction before expanding coverage.
                    continue
                result.append(Event(source="sec", source_id=accession, ticker=ticker,
                    event_type="filing", published_at=published, content_at=published,
                    received_at=datetime.now(UTC), source_url=url, text=content))
        return result


class SECCurrent:
    """8-K filings for the whole watchlist from EDGAR's current-filings feed: one request a poll, with tickers
    mapped to CIKs from the SEC's own list, so no per-company setup."""
    FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom&count=100"
    TICKERS = "https://www.sec.gov/files/company_tickers.json"

    def __init__(self, symbols, user_agent: str, http=None):
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must identify your organization and contact email")
        self.symbols = symbols
        self.http = http or HTTP({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, interval=0.25)
        self.ciks, self.loaded, self.seen = {}, None, set()

    async def poll(self, start, end):
        return await asyncio.to_thread(self.fetch, start, end)

    def fetch(self, start, end):
        now = datetime.now(UTC)
        if self.loaded is None or now - self.loaded > timedelta(days=1):
            self.ciks = {int(c["cik_str"]): c["ticker"] for c in reversed(self.http.get(self.TICKERS).json().values())}
            self.loaded = now  # reversed: a company's primary share class is listed first and wins
        atom = "{http://www.w3.org/2005/Atom}"
        result = []
        for entry in ElementTree.fromstring(self.http.get(self.FEED).content).findall(atom + "entry"):
            link = next((tag.get("href", "") for tag in entry.findall(atom + "link")), "")
            found = re.search(r"/data/(\d+)/\d+/(\d{10}-\d{2}-\d{6})-index", link)
            if not found or entry.findtext(atom + "title", "").split(" - ")[0] != "8-K":
                continue
            ticker, accession = self.ciks.get(int(found[1])), found[2]
            try:
                published = datetime.fromisoformat(entry.findtext(atom + "updated", ""))
            except ValueError:
                continue
            if (ticker not in self.symbols or accession in self.seen or published.utcoffset() is None
                    or not start <= published <= end):
                continue
            url = link.replace("-index.htm", ".txt")
            content = plain(self.http.get(url).text)
            if len(content) > 200_000:
                # Exhibit-heavy filings: the feed's item list ("Item 2.02: Results of Operations") still says what happened.
                content = plain(f"{entry.findtext(atom + 'title', '')} {entry.findtext(atom + 'summary', '')}")
            self.seen.add(accession)
            result.append(Event(source="sec", source_id=accession, ticker=ticker, event_type="filing",
                published_at=published, content_at=published, received_at=datetime.now(UTC),
                source_url=url, text=content))
        return result
