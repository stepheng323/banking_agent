"""Deterministic funding planner for multi-account transfers.

100% deterministic logic for financial safety.

Strategy: "Lazy Balance Fetching"
- Only fetch balances when needed (minimize API costs)
- If user specifies source account, use only that account
- Otherwise: try default first, then add more if needed
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from shared.clients.abstractions import DirectDebitProvider
from shared.formatters.funding import (
    format_funding_plan_message as _format_funding_plan_message,
)
from shared.formatters.funding import (
    format_insufficient_funds,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

MAX_SOURCE_ACCOUNTS = 2
MIN_FUNDING_AMOUNT = 100.0


@dataclass
class AccountBalance:
    """Account with current balance for planning."""

    account_id: UUID
    mono_account_id: str
    account_number: str
    bank_name: str
    mandate_id: str | None
    mandate_status: str
    is_default: bool
    available_balance: float = 0.0


@dataclass
class FundingStepPlan:
    """Planned debit from a single account."""

    account_id: UUID
    account_number: str
    bank_name: str
    mandate_id: str
    amount: float
    sequence: int


@dataclass
class FundingPlan:
    """Complete funding plan for a transfer."""

    transfer_amount: float
    total_funded: float
    steps: list[FundingStepPlan] = field(default_factory=list)
    is_sufficient: bool = False
    shortfall: float = 0.0
    error: str | None = None
    balance_checks: int = 0

    @property
    def num_sources(self) -> int:
        return len(self.steps)

    @property
    def is_single_source(self) -> bool:
        return len(self.steps) == 1

    @property
    def is_multi_source(self) -> bool:
        return len(self.steps) > 1


class FundingPlanner:
    """
    Deterministic funding planner with lazy balance fetching.
    Minimizes API calls by fetching balances only when needed.
    """

    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self._provider = direct_debit_provider

    async def plan_funding(
        self,
        accounts: list[Any],
        transfer_amount: float,
        preferred_account_id: UUID | None = None,
    ) -> FundingPlan:
        """
        Create a funding plan with lazy balance fetching.

        Args:
            accounts: Account model instances from database
            transfer_amount: Amount to fund in naira
            preferred_account_id: User-specified source account (optional)

        Returns:
            FundingPlan with steps or error
        """
        logger.info(
            "planning_funding",
            amount=transfer_amount,
            account_count=len(accounts),
            preferred_account_id=str(preferred_account_id) if preferred_account_id else None,
        )

        eligible = [a for a in accounts if self._is_eligible(a)]

        if not eligible:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                error="No accounts with active mandates.",
            )

        # Case 1: User specified a source account
        if preferred_account_id:
            return await self._plan_with_preferred_account(eligible, transfer_amount, preferred_account_id)

        # Case 2: Normal flow - try default first, then add if needed
        return await self._plan_with_lazy_fetching(eligible, transfer_amount)

    async def _plan_with_preferred_account(
        self,
        eligible: list[Any],
        transfer_amount: float,
        preferred_account_id: UUID,
    ) -> FundingPlan:
        """Plan using only the user-specified account."""
        account = next((a for a in eligible if a.id == preferred_account_id), None)

        if not account:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                error="Specified account not found or not eligible for direct debit.",
            )

        balance = await self._fetch_balance(account)

        if balance >= transfer_amount:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=transfer_amount,
                steps=[self._create_step(account, transfer_amount, 1)],
                is_sufficient=True,
                balance_checks=1,
            )
        else:
            shortfall = transfer_amount - balance
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=balance,
                shortfall=shortfall,
                is_sufficient=False,
                balance_checks=1,
                error=f"Your {account.bank_name} has ₦{balance:,.2f}. Need ₦{shortfall:,.2f} more.",
            )

    async def _plan_with_lazy_fetching(
        self,
        eligible: list[Any],
        transfer_amount: float,
    ) -> FundingPlan:
        """Plan with lazy balance fetching - default first, then largest."""
        steps: list[FundingStepPlan] = []
        remaining = transfer_amount
        balance_checks = 0
        sequence = 1

        # Sort: default first, then by is_default (we'll fetch balances lazily)
        default_accounts = [a for a in eligible if a.is_default]
        other_accounts = [a for a in eligible if not a.is_default]

        for account in default_accounts[:1]:
            if remaining <= 0:
                break

            balance = await self._fetch_balance(account)
            balance_checks += 1

            contribution = min(balance, remaining)
            if contribution >= MIN_FUNDING_AMOUNT or contribution >= remaining:
                steps.append(self._create_step(account, contribution, sequence))
                remaining -= contribution
                sequence += 1

        if remaining > 0 and len(steps) < MAX_SOURCE_ACCOUNTS:
            for account in other_accounts[: MAX_SOURCE_ACCOUNTS - len(steps)]:
                if remaining <= 0:
                    break

                balance = await self._fetch_balance(account)
                balance_checks += 1

                contribution = min(balance, remaining)
                if contribution >= MIN_FUNDING_AMOUNT or contribution >= remaining:
                    steps.append(self._create_step(account, contribution, sequence))
                    remaining -= contribution
                    sequence += 1

        total_funded = transfer_amount - max(0, remaining)
        is_sufficient = remaining <= 0

        plan = FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=total_funded,
            steps=steps,
            is_sufficient=is_sufficient,
            shortfall=max(0, remaining),
            balance_checks=balance_checks,
        )

        if not is_sufficient:
            if steps:
                primary = steps[0]
                total_available = sum(s.amount for s in steps)
                plan.error = format_insufficient_funds(
                    transfer_amount=transfer_amount,
                    bank_name=primary.bank_name,
                    available_balance=primary.amount,
                    max_available=total_available,
                )

        logger.info(
            "funding_plan_created",
            is_sufficient=is_sufficient,
            num_sources=len(steps),
            balance_checks=balance_checks,
        )

        return plan

    async def _fetch_balance(self, account: Any) -> float:
        """Fetch balance for a single account."""
        try:
            result = await self._provider.get_balance(account.mono_account_id, real_time=True)
            return result.available_balance if result.success else 0.0
        except Exception as e:
            logger.error("fetch_balance_failed", account_id=str(account.id), error=str(e))
            return 0.0

    def _create_step(self, account: Any, amount: float, sequence: int) -> FundingStepPlan:
        """Create a funding step from an account."""
        return FundingStepPlan(
            account_id=account.id,
            account_number=account.account_number,
            bank_name=account.bank_name,
            mandate_id=account.mandate_id,
            amount=amount,
            sequence=sequence,
        )

    def _is_eligible(self, account: Any) -> bool:
        """Check if account is eligible for debiting."""
        return account.mandate_status == "ready" and account.mandate_id is not None


def format_funding_plan_message(plan: FundingPlan) -> str:
    """Format funding plan for user display."""
    if not plan.is_sufficient:
        return plan.error or "Unable to create funding plan."

    steps = [{"bank_name": s.bank_name, "amount": s.amount} for s in plan.steps]
    return _format_funding_plan_message(
        transfer_amount=plan.transfer_amount,
        steps=steps,
    )
