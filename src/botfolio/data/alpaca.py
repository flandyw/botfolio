"""Alpaca market-data (and paper-account) REST client.

Free "Basic" market data limitations:
* Real-time/latest data is IEX-only (~2-3% of consolidated volume), so bars can differ from the
  consolidated tape and may skip quiet minutes.
* Historical SIP bars are available on the free plan except for the most recent 15 minutes.
* 200 API calls per minute.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests

from botfolio.config import Credentials
from botfolio.safety import (
    MARKET_DATA_URL,
    PAPER_TRADING_URL,
    assert_paper_account,
    assert_paper_url,
)

log = logging.getLogger(__name__)

BAR_COLUMNS = ["symbol", "ts", "open", "high", "low", "close", "volume"]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AlpacaAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class AlpacaClient:
    def __init__(self, creds: Credentials, timeout: float = 30.0, max_retries: int = 6) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "APCA-API-KEY-ID": creds.key_id,
                "APCA-API-SECRET-KEY": creds.secret_key,
                "Accept": "application/json",
            }
        )
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------ http
    def _request(self, method: str, url: str, **kw: Any) -> Any:
        delay = 1.0
        attempts = self.max_retries if method.upper() == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as exc:
                log.warning("alpaca request failed", extra={"url": url, "attempt": attempt, "err": str(exc)})
                if attempt == attempts:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == attempts:
                    raise AlpacaAPIError(resp.status_code, "Alpaca request failed; reconcile broker state before retrying")
                wait = float(resp.headers.get("Retry-After", delay))
                log.warning("alpaca throttled/server error", extra={"status": resp.status_code, "wait": wait})
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            if resp.status_code >= 400:
                raise AlpacaAPIError(resp.status_code, f"Alpaca {method} {url} -> {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else None
        raise RuntimeError(f"Alpaca request to {url} failed after {self.max_retries} retries")

    # ------------------------------------------------------------ market data
    def get_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
        feed: str = "iex",
        adjustment: str = "all",
    ) -> pd.DataFrame:
        """Historical bars for ``symbols`` in [start, end). Handles pagination."""
        url = f"{MARKET_DATA_URL}/v2/stocks/bars"
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": _iso(start),
            "end": _iso(end),
            "feed": feed,
            "adjustment": adjustment,
            "limit": 10000,
            "sort": "asc",
        }
        rows: list[tuple[Any, ...]] = []
        while True:
            data = self._request("GET", url, params=params) or {}
            for sym, bars in (data.get("bars") or {}).items():
                for b in bars:
                    rows.append((sym, b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]))
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        df = pd.DataFrame(rows, columns=BAR_COLUMNS)
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            df = df.drop_duplicates(["symbol", "ts"]).sort_values(["ts", "symbol"]).reset_index(drop=True)
        return df

    def get_paper_account(self) -> dict[str, Any]:
        url = f"{PAPER_TRADING_URL}/v2/account"
        assert_paper_url(url)
        acct = self._request("GET", url)
        assert_paper_account(acct)
        return acct
