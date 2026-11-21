"""Conservative event replay, paired controls and day-clustered uncertainty estimates."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from botfolio.config import CostConfig
from botfolio.data.calendar import frame_to_bars, get_calendar
from botfolio.events.models import Decision
from botfolio.execution.costs import CostModel

STRATEGIES = ("earnings", "breaking", "technical", "ai", "inverse")


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


def select(strategy, event, decision: Decision, context):
    side = decision.side()
    if strategy == "earnings" and (event.event_type not in {"earnings", "guidance", "transcript"}
                                   or decision.expectations_surprise == "unknown"):
        return 0
    if strategy == "breaking" and event.event_type not in {"news", "analyst", "regulatory", "filing"}:
        return 0
    if strategy == "technical" and not (side * context.get("return_5m", 0) > 0
                                       and context.get("volume_ratio", 0) >= 1.2):
        return 0
    return -side if strategy == "inverse" else side


def summary(rows, seed=42):
    if not rows:
        return dict(n=0, conclusion="insufficient evidence")
    frame = pd.DataFrame(rows).sort_values("exit")
    pnl = frame.net_pnl.to_numpy()
    days = frame.groupby("day").net_pnl.sum().to_numpy()
    rng = np.random.default_rng(seed)
    ci = np.quantile(rng.choice(days, (2000, len(days))).mean(axis=1), [.025, .975]) if len(days) >= 5 else None
    equity = np.r_[10000., 10000. + np.cumsum(pnl)]
    return dict(n=len(rows), total_return_pct=float(pnl.sum() / 100),
        max_drawdown_pct=float(np.max(1 - equity / np.maximum.accumulate(equity)) * 100),
        win_rate=float(np.mean(pnl > 0)), profit_factor=float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum())
            if np.any(pnl < 0) else None,
        average_net_pnl=float(np.mean(pnl)), fees_and_slippage=float(frame.costs.sum()),
        avg_net_return_pct=float(frame.return_pct.mean()),
        mean_daily_pnl_ci95=ci.tolist() if ci is not None else None,
        sharpe=None, sortino=None, annualized_return=None,
        exposure_dollar_minutes=float(frame.exposure.sum()),
        conclusion="exploratory; no significance claim; final test and multiple-testing correction required")


def replay(events, decisions, bars_df, *, train_end: datetime, validation_end: datetime,
           holding=(5, 15, 30, 60, 390), latency=30., costs=None, seed=42):
    if train_end >= validation_end or latency < 0 or any(h <= 0 for h in holding):
        raise ValueError("invalid chronological split, holding period or latency")
    bars = frame_to_bars(bars_df, get_calendar())
    by_symbol = {}
    for bar in bars:
        if not all(math.isfinite(x) and x > 0 for x in (bar.open, bar.high, bar.low, bar.close)):
            raise ValueError("invalid price")
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("inconsistent OHLC")
        if not math.isfinite(bar.volume) or bar.volume < 0:
            raise ValueError("invalid volume")
        by_symbol.setdefault(bar.symbol, []).append(bar)
    cost_model = CostModel(costs or CostConfig())
    rows, excluded, busy, daily, equity = [], [], {}, {}, {}
    def ordering(event):
        record = decisions.get(event.key)
        ready = (event.content_at if event.mode == "historical" else event.received_at) + timedelta(
            seconds=max(latency, record[1] if record else 0))
        if event.mode == "forward" and record:
            ready = max(ready, record[3])
        return ready, event.key

    for event in sorted(events, key=ordering):
        record = decisions.get(event.key)
        if record is None:
            excluded.append(dict(event=event.key, reason="no decision"))
            continue
        decision, measured_latency, model, completed = record
        decision.validate_event(event)
        try:
            available = event.available_at
        except ValueError as exc:
            excluded.append(dict(event=event.key, reason=str(exc)))
            continue
        ready = available + timedelta(seconds=max(latency, measured_latency))
        if event.mode == "forward":
            ready = max(ready, completed)
        symbol_bars = by_symbol.get(event.ticker, [])
        candidates = [b for b in symbol_bars if b.ts >= ready and b.volume > 0]
        if not candidates:
            excluded.append(dict(event=event.key, reason="no tradable post-availability bar"))
            continue
        entry = candidates[0]
        # At most the next regular session; do not execute a days-old event after a long missing-data interval.
        sessions = get_calendar().sessions_between(ready.date(), entry.ts.date())
        if len(sessions) > 2:
            excluded.append(dict(event=event.key, reason="stale event / missing entry data"))
            continue
        context = market_context(bars, event, available)
        for hold in holding:
            deadline = min(entry.ts + timedelta(minutes=hold), get_calendar().session(entry.session).close)
            path = [b for b in symbol_bars if entry.ts <= b.ts and b.end <= deadline]
            expected = int((deadline - entry.ts).total_seconds() / 60)
            if not path or len(path) != expected or any(
                b.ts != entry.ts + timedelta(minutes=i) or b.volume <= 0 for i, b in enumerate(path)
            ):
                excluded.append(dict(event=event.key, hold=hold, reason="missing bars or possible halt; no assumed fill"))
                continue
            # Purge events whose label window straddles a split boundary.
            if any(entry.ts <= boundary < deadline for boundary in (train_end, validation_end)):
                excluded.append(dict(event=event.key, hold=hold, reason="label crosses split boundary"))
                continue
            split = "train" if entry.ts <= train_end else "validation" if entry.ts <= validation_end else "test"
            for strategy in STRATEGIES:
                side = select(strategy, event, decision, context)
                name = f"{strategy}:{hold}:{split}"
                daykey = (name, str(entry.session))
                capital = equity.get(name, 10000.)
                if not side or busy.get(name, entry.ts) > entry.ts or daily.get(daykey, 0) <= -100:
                    continue
                fill = cost_model.fill_price(entry.open, side, "market")
                stop_dist = fill * .005
                qty = math.floor(min(capital * .0025 / (stop_dist + fill*.001 + .02), capital * .25 / fill))
                if qty < 1:
                    continue
                stop = fill - side * stop_dist
                last, ref, reason = path[-1], path[-1].close, "time/session"
                order_type = "market"
                for bar in path:
                    if (side > 0 and bar.low <= stop) or (side < 0 and bar.high >= stop):
                        last = bar
                        ref = min(stop, bar.open) if side > 0 else max(stop, bar.open)
                        order_type, reason = "stop", "stop"
                        break
                exit_fill = cost_model.fill_price(ref, -side, order_type)
                fees = cost_model.fees(qty, fill, side) + cost_model.fees(qty, exit_fill, -side)
                gross = side * qty * (ref - entry.open)
                net = side * qty * (exit_fill - fill) - fees
                daily[daykey] = daily.get(daykey, 0) + net
                equity[name] = capital + net
                busy[name] = last.end
                base = dict(event=event.key, ticker=event.ticker, event_type=event.event_type,
                    model=model, sentiment=decision.fundamental_impact, hold=hold, split=split,
                    day=str(entry.session), entry=entry.ts.isoformat(), exit=last.end.isoformat(),
                    qty=qty, side=side, strategy=strategy, net_pnl=net, costs=gross-net,
                    return_pct=100 * net / (qty * entry.open), reason=reason,
                    exposure=qty * fill * (last.end-entry.ts).total_seconds()/60)
                rows.append(base)
                random_side = 1 if int(hashlib.sha256(f"{seed}:{event.key}:{hold}:{strategy}".encode()).hexdigest(), 16) % 2 else -1
                random_fill = cost_model.fill_price(entry.open, random_side, "market")
                # Matched counterfactual: identical event, shares and exit instant/reference; no independent stop.
                random_exit = cost_model.fill_price(ref, -random_side, order_type)
                random_net = random_side * qty * (random_exit-random_fill) - cost_model.fees(qty, random_fill, random_side) - cost_model.fees(qty, random_exit, -random_side)
                rows.append({**base, "strategy": "random:" + strategy, "side": random_side,
                    "net_pnl": random_net, "return_pct": 100*random_net/(qty*entry.open),
                    "costs": random_side*qty*(ref-entry.open)-random_net})
    groups = {}
    for row in rows:
        key = f"{row['strategy']}:{row['hold']}:{row['split']}"
        groups.setdefault(key, []).append(row)
    return dict(config=dict(train_end=train_end.isoformat(), validation_end=validation_end.isoformat(),
        latency_floor_seconds=latency, holding=list(holding), seed=seed,
        costs=(costs or CostConfig()).model_dump()), trades=rows, exclusions=excluded,
        summaries={key: summary(value, seed) for key, value in groups.items()},
        warning="Retrospective AI may know outcomes. Verified content does not remove model-training leakage. "
        "Drawdown uses closed equity only. Random controls are paired counterfactuals, not independent portfolios. "
        "Sharpe/Sortino/annualization unavailable without a full marked-to-market daily equity series.")
