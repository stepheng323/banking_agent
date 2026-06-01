"""Data models for batch funding coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from banking.transfers.funding.models import FundingPlan
from shared.money import MoneyAmount


@dataclass
class SourceAffinity:
    mode: Literal["explicit", "auto"] = "auto"


@dataclass
class TransferDemand:
    """One transfer's funding demand."""

    task_id: str
    amount: MoneyAmount
    source_affinity: SourceAffinity = field(default_factory=SourceAffinity)
    explicit_sources: list[str] = field(default_factory=list)
    explicit_split: dict[str, MoneyAmount] | None = None
    use_dual_accounts: bool = False
    preferred_account_id: str | None = None


@dataclass
class ShortfallDetail:
    task_id: str
    amount_needed: MoneyAmount
    account_requested: str
    account_available: MoneyAmount
    deficit: MoneyAmount
    alternate_accounts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BatchFundingResult:
    """Result of batch funding coordination."""

    is_feasible: bool
    plans_by_task: dict[str, FundingPlan] = field(default_factory=dict)
    shortfalls: list[ShortfallDetail] | None = None
    total_demanded: MoneyAmount = Decimal("0.00")
    total_available: MoneyAmount = Decimal("0.00")
    suggestion: str | None = None


class BatchFundingAccount:
    """Minimal account view needed by batch funding allocation."""

    def __init__(self, data: dict[str, Any]):
        raw_id = data.get("id")
        self.id: UUID | None = UUID(raw_id) if isinstance(raw_id, str) else raw_id if isinstance(raw_id, UUID) else None
        self.mono_account_id: str = str(data.get("mono_account_id") or data.get("account_id") or "")
        self.account_number: str = str(data.get("account_number", ""))
        self.bank_name: str = str(data.get("bank_name", ""))
        self.mandate_status: str = str(data.get("mandate_status", "pending"))
        self.is_default = bool(data.get("is_default", False))
