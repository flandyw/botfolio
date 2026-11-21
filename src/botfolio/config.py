"""Typed configuration loaded from YAML, with credentials/paths overridden by environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


class DataConfig(BaseModel):
    feed: Literal["iex", "sip"] = "iex"


class NewsConfig(BaseModel):
    enabled: bool = True
    max_positions: int = Field(5, ge=1)
    position_pct: float = Field(25.0, gt=0, le=100)
    risk_pct: float = Field(1.0, gt=0, le=5)


class MomentumConfig(BaseModel):
    enabled: bool = True
    max_positions: int = Field(5, ge=1)
    position_pct: float = Field(10.0, gt=0, le=100)
    risk_pct: float = Field(0.5, gt=0, le=5)
    lookback: int = Field(14, ge=2)


class TrendConfig(BaseModel):
    enabled: bool = True
    allocation_pct: float = Field(50.0, gt=0, le=100)
    sma_days: int = Field(200, ge=2)
    band_pct: float = Field(1.0, ge=0)


class RotationConfig(BaseModel):
    enabled: bool = False
    allocation_pct: float = Field(40.0, gt=0, le=100)
    top: int = Field(5, ge=1)        # ETFs held, each with allocation_pct / top of equity
    keep: int = Field(10, ge=1)      # a holding is kept until it falls out of this many top ranks
    sma_days: int = Field(200, ge=2)


class CashParkConfig(BaseModel):
    enabled: bool = False
    symbol: str = "BOXX"
    reserve_pct: float = Field(5.0, ge=0, le=100)  # equity left in cash
    band_pct: float = Field(5.0, gt=0, le=100)     # resize once the position is this far from its target


class StrategiesConfig(BaseModel):
    news: NewsConfig = Field(default_factory=NewsConfig)
    momentum: MomentumConfig = Field(default_factory=MomentumConfig)
    etf_trend: TrendConfig = Field(default_factory=TrendConfig)
    etf_rotation: RotationConfig = Field(default_factory=RotationConfig)
    cash_park: CashParkConfig = Field(default_factory=CashParkConfig)


class AppConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["QQQ", "SPY"])
    etfs: list[str] = Field(default_factory=lambda: ["QQQ", "SPY"])
    data: DataConfig = Field(default_factory=DataConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    db_path: str = "data/botfolio.sqlite"
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("symbols", "etfs")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(s.strip().upper() for s in v if s.strip()))

    def limits(self) -> dict[str, dict[str, float]]:
        """Per-strategy position caps used by the paper executor."""
        s = self.strategies
        return {
            "news": dict(max_positions=s.news.max_positions, position_pct=s.news.position_pct,
                         risk_pct=s.news.risk_pct),
            "momentum": dict(max_positions=s.momentum.max_positions, position_pct=s.momentum.position_pct,
                             risk_pct=s.momentum.risk_pct),
            "etf_trend": dict(max_positions=max(1, len(self.etfs)),
                              position_pct=s.etf_trend.allocation_pct / max(1, len(self.etfs))),
            "etf_rotation": dict(max_positions=s.etf_rotation.top,
                                 position_pct=s.etf_rotation.allocation_pct / s.etf_rotation.top),
            "cash_park": dict(max_positions=1, position_pct=100 - s.cash_park.reserve_pct),
        }


class Credentials(BaseModel):
    key_id: str
    secret_key: str

    def __repr__(self) -> str:  # never leak secrets into logs
        return f"Credentials(key_id={self.key_id[:4]}…)"

    __str__ = __repr__


def watchlist(storage: Any, cfg: AppConfig) -> list[str]:
    """Symbols every strategy trades: the control-room list, else the config plus any older saved list."""
    saved = storage.get("watchlist")
    if saved:
        return list(saved)
    return list(dict.fromkeys([*cfg.symbols, *(storage.get("event-watchlist") or [])]))


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load YAML config (BOTFOLIO_CONFIG env or default), then apply environment overrides."""
    explicit = path or os.environ.get("BOTFOLIO_CONFIG")
    candidates = [Path(explicit)] if explicit else [Path("config/default.yaml"), DEFAULT_CONFIG_PATH]
    cfg_path = next((p for p in candidates if p.exists()), None)
    if cfg_path is None:
        raise FileNotFoundError(f"config file not found (tried {[str(p) for p in candidates]})")
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) or {}
    cfg = AppConfig.model_validate(raw)

    env_map = {
        "BOTFOLIO_DB_PATH": "db_path",
        "BOTFOLIO_LOG_LEVEL": "log_level",
    }
    updates: dict[str, Any] = {}
    for env, field in env_map.items():
        if os.environ.get(env):
            updates[field] = os.environ[env]
    if (lj := _env_bool("BOTFOLIO_LOG_JSON")) is not None:
        updates["log_json"] = lj
    if os.environ.get("BOTFOLIO_SYMBOLS"):
        updates["symbols"] = os.environ["BOTFOLIO_SYMBOLS"].split(",")
    if os.environ.get("BOTFOLIO_DATA_FEED"):
        updates["data"] = cfg.data.model_copy(update={"feed": os.environ["BOTFOLIO_DATA_FEED"].lower()})
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
