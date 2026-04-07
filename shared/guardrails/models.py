"""Typed models for runtime domain guardrails."""

from pydantic import BaseModel, Field


class NameMatchGuardrails(BaseModel):
    """Name resolution guardrails for transfer confirmation."""

    min_similarity: float = 0.65


class DynamicRiskGuardrails(BaseModel):
    """Dynamic risk threshold controls for transfer confirmation."""

    floor_amount: float = 50000.0
    lookback_days: int = 90
    percentile: float = 0.9


class TransferGuardrails(BaseModel):
    """Transfer-specific policy guardrails."""

    relational_aliases: list[str] = Field(
        default_factory=lambda: [
            "dad",
            "daddy",
            "mum",
            "mom",
            "mummy",
            "brother",
            "sister",
            "babe",
            "wife",
            "husband",
            "aunty",
            "uncle",
        ]
    )
    name_match: NameMatchGuardrails = Field(default_factory=NameMatchGuardrails)
    dynamic_risk: DynamicRiskGuardrails = Field(default_factory=DynamicRiskGuardrails)


class QueryGuardrails(BaseModel):
    """Query product limits."""

    max_lookback_days: int = 180
    max_results: int = 50
    max_group_buckets: int = 20
    max_narration_query_len: int = 40
    default_lookback_days: int = 30


class SupportGuardrails(BaseModel):
    """Support operational thresholds."""

    max_escalation_attempts: int = 3
    max_tx_lookback_days: int = 90
    sla_pending_hours: int = 24


class DomainGuardrails(BaseModel):
    """Top-level runtime guardrails loaded from JSON."""

    version: str = "1.0.0"
    last_updated: str | None = None
    transfer: TransferGuardrails = Field(default_factory=TransferGuardrails)
    query: QueryGuardrails = Field(default_factory=QueryGuardrails)
    support: SupportGuardrails = Field(default_factory=SupportGuardrails)
