"""Hard guards that make real-money trading impossible.

Defence in depth:
1. The only trading endpoint in the codebase is the constant PAPER_TRADING_URL. It is not configurable.
2. ``assert_paper_url`` rejects any other host before every trading request.
3. ``assert_paper_account`` verifies the account returned by Alpaca is a paper account
   (paper account numbers start with "PA") before any order is ever submitted.
4. Environment variables that commonly switch SDKs to live trading cause a hard failure.
5. Paper entries require explicit deployment settings and an armed control-room switch.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
MARKET_DATA_URL = "https://data.alpaca.markets"
_PAPER_HOST = "paper-api.alpaca.markets"
_LIVE_HOSTS = {"api.alpaca.markets"}


class LiveTradingForbidden(RuntimeError):
    """Raised whenever anything could result in a live (real-money) order."""


def assert_paper_url(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host != _PAPER_HOST:
        raise LiveTradingForbidden(f"Refusing trading request to non-paper host {host!r}")


def assert_paper_account(account: dict[str, Any]) -> None:
    number = str(account.get("account_number", ""))
    if not number.startswith("PA"):
        raise LiveTradingForbidden(
            "Alpaca account is not a paper account (account_number does not start with 'PA'); refusing to trade"
        )


def check_environment() -> None:
    """Fail fast if the environment tries to point anything at a live endpoint."""
    for var in ("APCA_API_BASE_URL", "ALPACA_BASE_URL", "ALPACA_TRADING_URL"):
        val = os.environ.get(var)
        if val:
            host = (urlparse(val).hostname or "").lower()
            if host in _LIVE_HOSTS or host != _PAPER_HOST:
                raise LiveTradingForbidden(f"{var}={val!r} is not the paper endpoint; live trading is disabled")
    for var in ("ALPACA_PAPER", "BOTFOLIO_PAPER"):
        val = os.environ.get(var)
        if val is not None and val.strip().lower() in {"0", "false", "no", "off"}:
            raise LiveTradingForbidden(f"{var}={val!r}: this application only supports paper trading")
