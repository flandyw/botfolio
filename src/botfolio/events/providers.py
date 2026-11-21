"""Polling providers. Archive actual responses as immutable event revisions."""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from html.parser import HTMLParser

import requests

from botfolio.data.alpaca import AlpacaClient
from botfolio.events.models import Event


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
                for ticker in sorted(set(article["symbols"]) & set(self.symbols)):
                    result.append(Event(source="alpaca:" + article.get("source", "unknown"),
                        source_id=str(article["id"]), ticker=ticker, event_type="news",
                        published_at=article["created_at"], content_at=article["updated_at"],
                        received_at=received, source_url=article["url"],
                        text=plain(article["headline"] + " " + article.get("content", "") + " "
                                   + article.get("summary", ""))))
            token = data.get("next_page_token")
            if not token:
                return result
            params["page_token"] = token
            time.sleep(0.35)


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
