"""Funding plan data models and limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

from shared.money import MoneyAmount

MIN_FUNDING_AMOUNT = Decimal("100.00")


@dataclass
class FundingStepPlan:
    """Planned debit from a single account."""

    account_id: UUID
    account_number: str
    bank_name: str
    amount: MoneyAmount
    sequence: int


@dataclass
class FundingPlan:
    """Complete funding plan for a transfer."""

    transfer_amount: MoneyAmount
    total_funded: MoneyAmount
    steps: list[FundingStepPlan] = field(default_factory=list)
    is_sufficient: bool = False
    shortfall: MoneyAmount = Decimal("0.00")
    error: str | None = None
    balance_checks: int = 0
    is_pending_mandate: bool = False
    trigger_mode: Literal["auto", "explicit"] = "auto"
    requested_sources: list[str] = field(default_factory=list)
    explicit_split_applied: bool = False
    primary_account_id: UUID | None = None
    primary_bank_name: str | None = None
    primary_available_balance: MoneyAmount | None = None

    @property
    def num_sources(self) -> int:
        return len(self.steps)

    @property
    def is_single_source(self) -> bool:
        return len(self.steps) == 1

    @property
    def is_multi_source(self) -> bool:
        return len(self.steps) > 1
