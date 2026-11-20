"""Strategy registry and roster construction."""

from __future__ import annotations

from typing import Any

from ttbot.config import AppConfig
from ttbot.strategies.base import Signal, Strategy
from ttbot.strategies.baselines import BuyAndHoldStrategy, InverseStrategy, RandomStrategy
from ttbot.strategies.classic import EMACrossStrategy, OpeningRangeBreakoutStrategy, RSIDivergenceStrategy
from ttbot.strategies.momentum import NoiseAreaMomentumStrategy
from ttbot.strategies.smc import (
    FVGStrategy,
    LiquiditySweepStrategy,
    OrderBlockStrategy,
    StructureBreakStrategy,
)

REGISTRY: dict[str, type[Strategy[Any]]] = {
    cls.kind: cls
    for cls in (
        FVGStrategy,
        LiquiditySweepStrategy,
        OrderBlockStrategy,
        StructureBreakStrategy,
        EMACrossStrategy,
        RSIDivergenceStrategy,
        OpeningRangeBreakoutStrategy,
        NoiseAreaMomentumStrategy,
        RandomStrategy,
    )
}

CONTROL_KINDS = {"random", "buy_hold", "inverse"}


def build_roster(cfg: AppConfig) -> list[Strategy[Any]]:
    """Instantiate every enabled strategy per timeframe, plus inverse, random and buy-and-hold controls."""
    roster: list[Strategy[Any]] = []
    atr_p = cfg.exits.atr_period
    for spec in cfg.strategies:
        if not spec.enabled:
            continue
        if spec.kind not in REGISTRY:
            raise ValueError(f"unknown strategy kind {spec.kind!r}")
        for tf in spec.timeframes:
            if tf not in cfg.timeframes:
                continue
            params = dict(spec.params)
            if spec.kind == "random":
                params.setdefault("seed", cfg.seed)
                params.setdefault("trades_per_day", cfg.random_baseline_trades_per_day)
            if "starting_capital" in spec.risk:
                raise ValueError(f"{spec.name}: starting_capital is global and cannot be overridden per strategy")
            exits = cfg.exits.model_copy(update=spec.exits) if spec.exits else None
            risk = cfg.risk.model_copy(update=spec.risk) if spec.risk else None
            strat = REGISTRY[spec.kind](f"{spec.name}_{tf}m", tf, params, spec.symbols, atr_p)
            strat.exits, strat.risk = exits, risk
            roster.append(strat)
            if cfg.include_inverse and spec.kind != "random":
                inner = REGISTRY[spec.kind](f"{spec.name}_{tf}m", tf, params, spec.symbols, atr_p)
                inner.exits, inner.risk = exits, risk
                roster.append(InverseStrategy(inner))
    if not any(s.kind == "random" for s in roster):
        for tf in cfg.timeframes:
            roster.append(
                RandomStrategy(
                    f"random_{tf}m",
                    tf,
                    {"seed": cfg.seed, "trades_per_day": cfg.random_baseline_trades_per_day},
                    None,
                    atr_p,
                )
            )
    for sym in cfg.symbols:
        roster.append(BuyAndHoldStrategy(f"buy_hold_{sym}", 1, {}, [sym], atr_p))
    names = [s.name for s in roster]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate strategy names in roster: {names}")
    return roster


def strategy_group(name: str) -> str:
    if name.startswith("buy_hold"):
        return "benchmark"
    if name.startswith("random"):
        return "random"
    if name.startswith("inv_"):
        return "inverse"
    return "pattern"


__all__ = ["REGISTRY", "Signal", "Strategy", "build_roster", "strategy_group"]
