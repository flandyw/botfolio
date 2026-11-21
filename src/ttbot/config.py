"""Typed configuration loaded from YAML, with credentials/paths overridden by environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


class CostConfig(BaseModel):
    """Trading-cost model. All values are per side unless stated."""

    half_spread_per_share: float = Field(0.005, ge=0, description="Half the quoted bid/ask spread in $.")
    slippage_bps: float = Field(1.0, ge=0, description="Market-order slippage beyond the half spread (bps).")
    stop_slippage_bps: float = Field(2.0, ge=0, description="Extra slippage when a stop order triggers (bps).")
    commission_per_share: float = Field(0.0, ge=0)
    min_commission: float = Field(0.0, ge=0)
    sec_fee_per_million: float = Field(27.80, ge=0, description="SEC Section 31 fee on SELL notional.")
    finra_taf_per_share: float = Field(0.000166, ge=0, description="FINRA TAF on SELL quantity.")
    finra_taf_max: float = Field(8.30, ge=0)


class RiskConfig(BaseModel):
    starting_capital: float = Field(100_000.0, gt=0)
    risk_per_trade_pct: float = Field(0.5, gt=0, le=10, description="% of equity lost if the stop is hit.")
    max_position_pct: float = Field(100.0, gt=0, le=400, description="Max notional per position, % of equity.")
    max_gross_exposure_pct: float = Field(100.0, gt=0, le=400)
    max_open_positions: int = Field(2, ge=1)
    max_trades_per_day: int = Field(10, ge=1)
    daily_loss_limit_pct: float = Field(2.0, gt=0, description="Stop opening trades for the day below this.")
    allow_short: bool = True
    sizing: Literal["risk", "notional"] = Field(
        "risk", description="risk: size so the stop loses risk_per_trade_pct; notional: max_position_pct of equity."
    )


class ExitConfig(BaseModel):
    """Stop-loss / take-profit rules applied identically to every pattern strategy."""

    stop_mode: Literal["structure", "atr", "none"] = "structure"  # none: no stop/target, strategy exits
    atr_period: int = Field(14, ge=2)
    atr_stop_mult: float = Field(1.5, gt=0, description="Stop distance in ATRs when stop_mode=atr.")
    min_stop_atr: float = Field(0.5, gt=0, description="Structure stops are clamped to [min, max] ATR.")
    max_stop_atr: float = Field(3.0, gt=0)
    reward_risk: float = Field(2.0, gt=0, description="Take-profit distance = reward_risk * stop distance.")
    max_hold_bars: int = Field(60, ge=1, description="Time stop in bars of the strategy timeframe.")
    flatten_eod: bool = True
    no_entry_last_minutes: int = Field(15, ge=0)


class SessionConfig(BaseModel):
    calendar: str = "XNYS"
    timezone: str = "America/New_York"


class DataConfig(BaseModel):
    feed: Literal["iex", "sip"] = "iex"
    adjustment: Literal["raw", "split", "all"] = "all"
    cache_bars: bool = True


class StrategySpec(BaseModel):
    """One entry in the strategy roster."""

    name: str
    kind: str
    timeframes: list[int] = Field(default_factory=lambda: [1, 5])
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    symbols: list[str] | None = Field(None, description="Restrict to these symbols (default: all).")
    exits: dict[str, Any] = Field(default_factory=dict, description="Per-strategy overrides of `exits`.")
    risk: dict[str, Any] = Field(default_factory=dict, description="Per-strategy overrides of `risk`.")


class AppConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["QQQ", "SPY"])
    timeframes: list[int] = Field(default_factory=lambda: [1, 5])
    seed: int = 42
    include_inverse: bool = True
    random_baseline_trades_per_day: float = Field(2.0, gt=0)
    monte_carlo_iterations: int = Field(2000, ge=100)
    costs: CostConfig = Field(default_factory=CostConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    strategies: list[StrategySpec] = Field(default_factory=list)

    # runtime (env) settings - never read from YAML secrets
    db_path: str = "data/ttbot.sqlite"
    log_level: str = "INFO"
    log_json: bool = True
    poll_seconds: int = Field(60, ge=10)
    bar_delay_seconds: int = Field(10, ge=0, description="Wait after a minute closes before fetching it.")
    mirror_strategy: str | None = Field(None, description="Optionally mirror ONE portfolio to Alpaca paper.")

    @field_validator("symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s.strip()]

    @field_validator("timeframes")
    @classmethod
    def _tfs(cls, v: list[int]) -> list[int]:
        for tf in v:
            if tf not in (1, 5):
                raise ValueError("Only 1- and 5-minute timeframes are supported")
        return sorted(set(v))


class Credentials(BaseModel):
    key_id: str
    secret_key: str

    def __repr__(self) -> str:  # never leak secrets into logs
        return f"Credentials(key_id={self.key_id[:4]}…)"

    __str__ = __repr__


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load YAML config (TTBOT_CONFIG env or default), then apply environment overrides."""
    explicit = path or os.environ.get("TTBOT_CONFIG")
    candidates = [Path(explicit)] if explicit else [Path("config/default.yaml"), DEFAULT_CONFIG_PATH]
    cfg_path = next((p for p in candidates if p.exists()), None)
    if cfg_path is None:
        raise FileNotFoundError(f"config file not found (tried {[str(p) for p in candidates]})")
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) or {}
    if not raw.get("strategies"):
        raise ValueError(f"{cfg_path}: no strategies configured")
    cfg = AppConfig.model_validate(raw)

    env_map = {
        "TTBOT_DB_PATH": "db_path",
        "TTBOT_LOG_LEVEL": "log_level",
        "TTBOT_POLL_SECONDS": "poll_seconds",
        "TTBOT_MIRROR_STRATEGY": "mirror_strategy",
    }
    updates: dict[str, Any] = {}
    for env, field in env_map.items():
        if os.environ.get(env):
            updates[field] = os.environ[env]
    if (lj := _env_bool("TTBOT_LOG_JSON")) is not None:
        updates["log_json"] = lj
    if os.environ.get("TTBOT_SYMBOLS"):
        updates["symbols"] = os.environ["TTBOT_SYMBOLS"].split(",")
    if os.environ.get("TTBOT_DATA_FEED"):
        updates["data"] = cfg.data.model_copy(update={"feed": os.environ["TTBOT_DATA_FEED"].lower()})
    if updates:
        cfg = AppConfig.model_validate({**cfg.model_dump(), **updates})
    return cfg


def load_credentials(required: bool = True) -> Credentials | None:
    """Read Alpaca credentials from the environment (.env is loaded by the CLI).

    Accepts ALPACA_KEY/ALPACA_SECRET or the official APCA_API_KEY_ID/APCA_API_SECRET_KEY names.
    """
    key = os.environ.get("ALPACA_KEY") or os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("ALPACA_SECRET") or os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        if required:
            raise RuntimeError(
                "Alpaca credentials missing: set ALPACA_KEY and ALPACA_SECRET (paper keys) in the environment/.env"
            )
        return None
    return Credentials(key_id=key.strip(), secret_key=secret.strip())
