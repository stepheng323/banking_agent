"""Data models for batch funding coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from banking.transfers.funding.identifiers import coerce_account_id
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
    source_pooling_locked: bool = False


@dataclass
class ShortfallDetail:
    task_id: str
    amount_needed: MoneyAmount
    account_requested: str
    account_available: MoneyAmount
    deficit: MoneyAmount
    alternate_accounts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FundingSourceOption:
    """User-facing source account balance option for funding review prompts."""

    account_id: str
    bank_name: str
    account_number: str
    available: MoneyAmount
    last4: str = ""
    is_default: bool = False
    is_selected: bool = False


@dataclass
class FundingSourceChoice:
    """Clarification needed when multiple source accounts can cover an implicit funding gap."""

    remaining_amount: MoneyAmount
    candidate_source_ids: list[str] = field(default_factory=list)


@dataclass
class BatchFundingResult:
    """Result of batch funding coordination."""

    is_feasible: bool
    plans_by_task: dict[str, FundingPlan] = field(default_factory=dict)
    suggested_plans_by_task: dict[str, FundingPlan] = field(default_factory=dict)
    shortfalls: list[ShortfallDetail] | None = None
    total_demanded: MoneyAmount = Decimal("0.00")
    total_available: MoneyAmount = Decimal("0.00")
    suggestion: str | None = None
    requires_user_approval: bool = False
    requires_source_choice: bool = False
    source_choice: FundingSourceChoice | None = None
    source_options: list[FundingSourceOption] = field(default_factory=list)
    anchor_source_ids: list[str] = field(default_factory=list)
    suggested_source_ids: list[str] = field(default_factory=list)
    capped_available: MoneyAmount = Decimal("0.00")
    funding_shortfall: MoneyAmount = Decimal("0.00")


class BatchFundingAccount:
    """Minimal account view needed by batch funding allocation."""

    def __init__(self, data: dict[str, Any]):
        raw_id = data.get("id")
        self.id: UUID | str | None = coerce_account_id(raw_id)
        self.mono_account_id: str = str(data.get("mono_account_id") or data.get("account_id") or "")
        self.account_number: str = _account_display_number(data)
        self.last4: str = str(data.get("last4", data.get("account_number_last4", "")))
        self.bank_name: str = str(data.get("bank_name", ""))
        self.mandate_status: str = str(data.get("mandate_status", "pending"))
        self.is_default = bool(data.get("is_default", False))


def _account_display_number(data: dict[str, Any]) -> str:
    for key in (
        "account_number",
        "number",
        "source_account_number",
        "account_number_last4",
        "source_account_number_last4",
        "last4",
    ):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""
