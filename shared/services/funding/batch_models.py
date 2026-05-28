"""Data models for batch funding coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from shared.services.funding.models import FundingPlan


@dataclass
class SourceAffinity:
    mode: Literal["explicit", "auto"] = "auto"


@dataclass
class TransferDemand:
    """One transfer's funding demand."""

    task_id: str
    amount: float
    source_affinity: SourceAffinity = field(default_factory=SourceAffinity)
    explicit_sources: list[str] = field(default_factory=list)
    explicit_split: dict[str, float] | None = None
    use_dual_accounts: bool = False
    preferred_account_id: str | None = None


@dataclass
class ShortfallDetail:
    task_id: str
    amount_needed: float
    account_requested: str
    account_available: float
    deficit: float
    alternate_accounts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BatchFundingResult:
    """Result of batch funding coordination."""

    is_feasible: bool
    plans_by_task: dict[str, FundingPlan] = field(default_factory=dict)
    shortfalls: list[ShortfallDetail] | None = None
    total_demanded: float = 0.0
    total_available: float = 0.0
    suggestion: str | None = None


class BatchFundingAccount:
    """Minimal account view needed by batch funding allocation."""

    def __init__(self, data: dict[str, Any]):
        raw_id = data.get("id")
        self.id = UUID(raw_id) if isinstance(raw_id, str) else raw_id
        self.mono_account_id = data.get("mono_account_id") or data.get("account_id") or ""
        self.account_number = data.get("account_number", "")
        self.bank_name = data.get("bank_name", "")
        self.mandate_id = data.get("mandate_id")
        self.mandate_status = data.get("mandate_status", "pending")
        self.is_default = bool(data.get("is_default", False))
