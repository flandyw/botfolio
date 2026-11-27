"""Daily risk regime: Claude searches the web for market news and picks how much of the max size to trade today."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta

from botfolio.data.calendar import get_calendar, ny_date
from botfolio.events.ai import AI

log = logging.getLogger(__name__)
SCALED = ("news", "momentum", "vwap_reversion", "orb")  # intraday strategies whose position_pct and risk_pct the regime scales
SCHEMA = {"type": "object", "required": ["risk_scale", "reasoning", "key_news"], "properties": {
    "risk_scale": {"type": "number", "minimum": 0, "maximum": 1},
    "reasoning": {"type": "string"},
    "key_news": {"type": "array", "items": {"type": "string"}}}}
PROMPT = """You set the daily risk level for an intraday US equity paper-trading bot (news-driven bracket trades on
large caps, SPY/QQQ breakout momentum, opening-range breakouts and VWAP mean reversion, long and short, flat by
the close). Today is {day} (New York).

Use web search to review ALL market-moving news for today's US session: overnight futures and global markets,
macro data and Fed speakers scheduled today, earnings, geopolitics, VIX level, sector moves, and anything unusual.

Choose risk_scale from 0 to 1, the fraction of the bot's maximum position size to use today:
- 1.0: clean trending or catalyst-rich tape where breakouts and news reactions tend to follow through.
- ~0.5: mixed or choppy conditions.
- 0.1-0.3: very high uncertainty (a major release mid-session, whipsaw risk, holiday thin liquidity).
- 0: only if trading today is clearly a bad idea.
The account is paper money and the owner wants aggressive risk, so do not default to caution: cut size only
for a concrete reason you found. Give short reasoning and the key headlines you relied on."""


def key(day) -> str:
    return f"risk-regime:{day}"


def scale(storage, now) -> float:
    """Today's scale, 1.0 (the configured max) when no assessment exists."""
    regime = storage.get(key(ny_date(now))) or {}
    return float(regime.get("risk_scale", 1.0))


def assess(ai: AI, day) -> dict:
    command = [ai.url, "-p", "--model", ai.model, "--output-format", "json",
               "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch",
               "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
               "--json-schema", json.dumps(SCHEMA)]
    done = subprocess.run(command, input=PROMPT.format(day=day), capture_output=True, text=True,
                          timeout=600, cwd=tempfile.gettempdir(), check=False)
    raw = json.loads(done.stdout)
    if raw.get("is_error") or "structured_output" not in raw:
        raise ValueError("claude risk call failed: " + str(raw.get("result", ""))[:200])
    out = raw["structured_output"]
    return dict(risk_scale=min(1.0, max(0.0, float(out["risk_scale"]))), reasoning=str(out["reasoning"]),
                key_news=[str(n) for n in out["key_news"]][:10], model=ai.model,
                at=datetime.now(UTC).isoformat())


async def risk_loop(storage, once=False):
    """Once per trading day, from an hour before the open, store Claude's risk scale. Retries every 30 minutes."""
    ai = AI("EVENT_AI_CLAUDE", "claude")
    if ai.provider == "disabled":
        log.info("risk regime disabled: set EVENT_AI_CLAUDE_MODEL")
        return
    while True:
        now = datetime.now(UTC)
        day = ny_date(now)
        session = get_calendar("NASDAQ").session(day)
        attempt = storage.get(key(day) + ":attempt")
        if (session and session.open - timedelta(hours=1) <= now < session.close and storage.get(key(day)) is None
                and (attempt is None or now - datetime.fromisoformat(attempt) > timedelta(minutes=30))):
            storage.put(key(day) + ":attempt", now.isoformat())
            try:
                regime = await asyncio.to_thread(assess, ai, day)
                storage.put(key(day), regime)
                log.info("risk regime set", extra=regime)
            except Exception:
                log.exception("risk regime failed; trading at the configured max until it succeeds")
        if once:
            return
        await asyncio.sleep(60)
