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
from shared.i18n import render_message
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
    is_pending_mandate: bool = False

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
        locale: str = "en",
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
            # Distinguish between "pending mandate" and "no mandate at all"
            pending_accounts = [
                a for a in accounts
                if getattr(a, "mandate_status", None) not in (None, "ready")
            ]
            if pending_accounts:
                error_msg = self._build_pending_mandate_message(pending_accounts[0], locale)
            else:
                error_msg = render_message("funding.planner.no_active_mandates", locale)
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                error=error_msg,
                is_pending_mandate=True if pending_accounts else False,
            )

        # Case 1: User specified a source account
        if preferred_account_id:
            return await self._plan_with_preferred_account(eligible, transfer_amount, preferred_account_id, locale)

        # Case 2: Normal flow - try default first, then add if needed
        return await self._plan_with_lazy_fetching(eligible, transfer_amount, locale)

    async def _plan_with_preferred_account(
        self,
        eligible: list[Any],
        transfer_amount: float,
        preferred_account_id: UUID,
        locale: str,
    ) -> FundingPlan:
        """Plan using only the user-specified account."""
        account = next((a for a in eligible if a.id == preferred_account_id), None)

        if not account:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                error=render_message("funding.planner.preferred_account_not_eligible", locale),
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
                error=render_message(
                    "funding.planner.preferred_insufficient",
                    locale,
                    {"bank_name": account.bank_name, "balance": f"{balance:,.2f}", "shortfall": f"{shortfall:,.2f}"},
                ),
            )

    async def _plan_with_lazy_fetching(
        self,
        eligible: list[Any],
        transfer_amount: float,
        locale: str,
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
                    locale=locale,
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

    def _build_pending_mandate_message(self, account: Any, locale: str) -> str:
        """Build contextual message for accounts with pending mandates.

        Delegates to MandateService.build_mandate_auth_message() to avoid duplication.
        """
        from shared.services.onboarding.mandate import MandateService

        extra_data: dict = getattr(account, "extra_data", None) or {}
        destinations: list[dict] = extra_data.get("transfer_destinations", [])
        account_number: str = getattr(account, "account_number", "") or ""
        bank_name: str = getattr(account, "bank_name", "") or ""

        if destinations:
            svc = MandateService(queue=None)  # type: ignore[arg-type]
            return svc.build_mandate_auth_message(
                account_number=account_number,
                bank_name=bank_name,
                transfer_destinations=destinations,
            )

        return render_message("mandate.pending_complete_transfer", locale)


def format_funding_plan_message(plan: FundingPlan, locale: str = "en") -> str:
    """Format funding plan for user display."""
    if not plan.is_sufficient:
        return plan.error or render_message("funding.planner.unable_to_create", locale)

    steps = [{"bank_name": s.bank_name, "amount": s.amount} for s in plan.steps]
    return _format_funding_plan_message(
        transfer_amount=plan.transfer_amount,
        steps=steps,
        locale=locale,
    )
