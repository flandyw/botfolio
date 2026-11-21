"""Market context available at the time a news event was received."""

import numpy as np


def market_context(bars, event, at):
    prior = [b for b in bars if b.symbol == event.ticker and b.end <= at][-31:]
    if len(prior) < 6 or any(b.ts != a.end for a, b in zip(prior[-6:-1], prior[-5:], strict=True)):
        return {"missing": "insufficient completed pre-decision bars"}
    returns = np.diff(np.log([b.close for b in prior]))
    result = dict(asof=at.isoformat(), last_bar_end=prior[-1].end.isoformat(),
        return_5m=prior[-1].close / prior[-6].close - 1,
        volatility=float(np.std(returns)), volume=prior[-1].volume,
        volume_ratio=prior[-1].volume / max(1, np.mean([b.volume for b in prior[:-1]])),
        market_return=None, sector_return=None, related_announcements=None)
    market = [b for b in bars if b.symbol == "SPY" and b.end <= at][-6:]
    if len(market) == 6:
        result["market_return"] = market[-1].close / market[0].close - 1
    return result
