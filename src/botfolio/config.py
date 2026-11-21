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


class AppConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["QQQ", "SPY"])
    data: DataConfig = Field(default_factory=DataConfig)
    db_path: str = "data/botfolio.sqlite"
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s.strip()]


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
