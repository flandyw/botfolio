from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    event_type: Literal["earnings", "guidance", "news", "analyst", "regulatory", "filing", "transcript"]
    published_at: AwareDatetime
    content_at: AwareDatetime  # timestamp of this content revision, never just the original headline
    received_at: AwareDatetime
    source_url: str = Field(pattern=r"^https://")
    text: str = Field(min_length=1, max_length=200000)
    mode: Literal["historical", "forward"] = "forward"
    snapshot_verified: bool = False
    actual_eps: float | None = None
    consensus_eps: float | None = None
    actual_revenue: float | None = None
    consensus_revenue: float | None = None
    consensus_at: AwareDatetime | None = None
    consensus_source: str | None = None

    @model_validator(mode="after")
    def timestamps(self):
        if not self.published_at <= self.content_at <= self.received_at:
            raise ValueError("require publication <= content revision <= actual receipt")
        if self.received_at > datetime.now(UTC):
            raise ValueError("receipt cannot be in the future")
        if self.consensus_eps is not None or self.consensus_revenue is not None:
            if not self.consensus_source or self.consensus_at is None or self.consensus_at >= self.published_at:
                raise ValueError("consensus requires a pre-publication timestamp and source")
        return self

    @property
    def key(self) -> str:
        return hashlib.sha256(json.dumps([self.source, self.source_id, self.ticker,
            self.content_at.astimezone(UTC).isoformat(), self.text]).encode()).hexdigest()



class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    ticker: str
    event_type: str
    event_timestamp: AwareDatetime
    source_url: str
    fundamental_impact: Literal["positive", "negative", "neutral", "unknown"]
    materiality: Literal["high", "medium", "low", "unknown"]
    expectations_surprise: Literal["positive", "negative", "neutral", "unknown"]
    uncertainty: Literal["high", "medium", "low"]
    directional_hypothesis: Literal["bullish", "bearish", "neutral", "unknown"]
    confidence: None = None  # model scores are not probabilities of profitable trades
    reasoning: str = Field(min_length=1)
    key_evidence: list[str]
    risk_factors: list[str]
    recommended_action: Literal["BUY", "SELL", "HOLD", "WAIT"]

    def validate_event(self, event: Event) -> None:
        if (self.ticker, self.event_type, self.event_timestamp, self.source_url) != (
            event.ticker, event.event_type, event.published_at, event.source_url
        ):
            raise ValueError("decision identity does not match event")
        if any(evidence not in event.text for evidence in self.key_evidence):
            raise ValueError("key evidence must be exact excerpts from the archived event")

    def side(self) -> int:
        # Aggressive paper mode: follow the direction call; the model almost always says WAIT/high uncertainty.
        if self.materiality not in {"high", "medium"} or not self.key_evidence:
            return 0
        if self.directional_hypothesis == "bullish" and self.recommended_action != "SELL":
            return 1
        if self.directional_hypothesis == "bearish" and self.recommended_action != "BUY":
            return -1
        return 0
