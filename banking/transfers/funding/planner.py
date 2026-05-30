"""Deterministic funding planner for multi-account transfers.

100% deterministic logic for financial safety.

Strategy: "Lazy Balance Fetching"
- Only fetch balances when needed (minimize API costs)
- If user specifies source account, use only that account
- Otherwise: try default first, then add more if needed
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

import banking.transfers.funding.account_matching as account_matching
import banking.transfers.funding.explicit_pooling as explicit_pooling
import banking.transfers.funding.models as funding_models
from banking.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS
from banking.presentation.formatters.funding import (
    format_insufficient_funds,
)
from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.money import MoneyAmount, require_naira, to_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FundingPlanner:
    """
    Deterministic funding planner with lazy balance fetching.
    Minimizes API calls by fetching balances only when needed.
    """

    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self._provider = direct_debit_provider
        self._balance_overrides: dict[str, MoneyAmount] | None = None

    async def plan_funding(
        self,
        accounts: list[Any],
        transfer_amount: MoneyAmount,
        preferred_account_id: UUID | None = None,
        use_dual_accounts: bool | None = None,
        requested_source_banks: list[str] | None = None,
        explicit_split: dict[str, MoneyAmount] | None = None,
        locale: str = "en",
        balance_overrides: dict[str, MoneyAmount] | None = None,
    ) -> funding_models.FundingPlan:
        """
        Create a funding plan with lazy balance fetching.

        Args:
            accounts: Account model instances from database
            transfer_amount: Amount to fund in naira
            preferred_account_id: User-specified source account (optional)

        Returns:
            funding_models.FundingPlan with steps or error
        """
        transfer_amount = require_naira(transfer_amount)
        logger.info(
            "planning_funding",
            amount=str(transfer_amount),
            account_count=len(accounts),
            preferred_account_id=str(preferred_account_id) if preferred_account_id else None,
            use_dual_accounts=bool(use_dual_accounts),
            requested_source_banks=requested_source_banks or [],
            has_explicit_split=bool(explicit_split),
            has_balance_overrides=bool(balance_overrides),
        )

        old_overrides = self._balance_overrides
        self._balance_overrides = {
            account_id: amount
            for account_id, raw_amount in (balance_overrides or {}).items()
            if (amount := to_naira(raw_amount)) is not None
        } or None
        try:
            eligible = [a for a in accounts if account_matching.is_eligible(a)]

            if not eligible:
                # Distinguish between "pending mandate" and "no mandate at all"
                pending_accounts = [a for a in accounts if getattr(a, "mandate_status", None) not in (None, "ready")]
                if pending_accounts:
                    error_msg = account_matching.build_pending_mandate_message_for_account(pending_accounts[0], locale)
                else:
                    error_msg = render_message("funding.planner.no_active_mandates", locale)
                return funding_models.FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=Decimal("0.00"),
                    is_sufficient=False,
                    error=error_msg,
                    is_pending_mandate=bool(pending_accounts),
                )

            normalized_requested_sources = [
                s for s in (requested_source_banks or []) if isinstance(s, str) and s.strip()
            ]
            cleaned_explicit_split = {
                bank: parsed_amount
                for bank, amount in (explicit_split or {}).items()
                if isinstance(bank, str)
                and bank.strip()
                and (parsed_amount := to_naira(amount)) is not None
                and parsed_amount > 0
            }

            # Case 1: Explicit pooling request takes precedence
            if cleaned_explicit_split or use_dual_accounts or normalized_requested_sources:
                return await explicit_pooling.plan_explicit_pooling(
                    eligible=eligible,
                    all_accounts=accounts,
                    transfer_amount=transfer_amount,
                    requested_source_banks=normalized_requested_sources,
                    explicit_split=cleaned_explicit_split,
                    force_multi_source=bool(use_dual_accounts or normalized_requested_sources),
                    locale=locale,
                    fetch_balance=self._fetch_balance,
                )

            # Case 2: User specified a source account
            if preferred_account_id:
                return await self._plan_with_preferred_account(
                    eligible,
                    accounts,
                    transfer_amount,
                    preferred_account_id,
                    locale,
                )

            # Case 3: Normal flow - try default first, then add if needed
            return await self._plan_with_lazy_fetching(eligible, transfer_amount, locale)
        finally:
            self._balance_overrides = old_overrides

    async def _plan_with_preferred_account(
        self,
        eligible: list[Any],
        all_accounts: list[Any],
        transfer_amount: MoneyAmount,
        preferred_account_id: UUID,
        locale: str,
    ) -> funding_models.FundingPlan:
        """Plan using preferred account first, then pool if needed."""
        account = next((a for a in eligible if a.id == preferred_account_id), None)

        if not account:
            pending_match = next((a for a in all_accounts if getattr(a, "id", None) == preferred_account_id), None)
            if pending_match is not None and not account_matching.is_eligible(pending_match):
                return funding_models.FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=Decimal("0.00"),
                    is_sufficient=False,
                    trigger_mode="explicit",
                    error=account_matching.build_explicit_nonready_account_message(pending_match, locale),
                )
            return funding_models.FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=Decimal("0.00"),
                is_sufficient=False,
                error=render_message("funding.planner.preferred_account_not_eligible", locale),
            )

        balance = await self._fetch_balance(account)
        primary_balance = balance

        if balance >= transfer_amount:
            return funding_models.FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=transfer_amount,
                steps=[account_matching.create_step(account, transfer_amount, 1)],
                is_sufficient=True,
                balance_checks=1,
                trigger_mode="auto",
                primary_account_id=account.id,
                primary_bank_name=account.bank_name,
                primary_available_balance=primary_balance,
            )

        steps = [account_matching.create_step(account, balance, 1)] if balance > 0 else []
        remaining = max(Decimal("0.00"), transfer_amount - balance)
        balance_checks = 1
        sequence = 2

        others = [candidate for candidate in eligible if candidate.id != preferred_account_id]
        for candidate in others[: max(0, MAX_POOLED_SOURCE_ACCOUNTS - len(steps))]:
            if remaining <= 0:
                break
            available = await self._fetch_balance(candidate)
            balance_checks += 1
            contribution = min(available, remaining)
            if contribution > 0:
                steps.append(account_matching.create_step(candidate, contribution, sequence))
                remaining -= contribution
                sequence += 1

        total_funded = transfer_amount - max(Decimal("0.00"), remaining)
        plan = funding_models.FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=total_funded,
            steps=steps,
            is_sufficient=remaining <= 0,
            shortfall=max(Decimal("0.00"), remaining),
            balance_checks=balance_checks,
            trigger_mode="auto",
            primary_account_id=account.id,
            primary_bank_name=account.bank_name,
            primary_available_balance=primary_balance,
        )
        if not plan.is_sufficient:
            plan.error = render_message(
                "funding.planner.preferred_insufficient",
                locale,
                {"bank_name": account.bank_name, "balance": f"{balance:,.2f}", "shortfall": f"{remaining:,.2f}"},
            )
        return plan

    async def _plan_with_lazy_fetching(
        self,
        eligible: list[Any],
        transfer_amount: MoneyAmount,
        locale: str,
    ) -> funding_models.FundingPlan:
        """Plan with lazy balance fetching - default first, then largest."""
        steps: list[funding_models.FundingStepPlan] = []
        remaining = transfer_amount
        balance_checks = 0
        sequence = 1
        balances_by_account: dict[str, MoneyAmount] = {}

        # Sort: default first, then by is_default (we'll fetch balances lazily)
        default_accounts = [a for a in eligible if a.is_default]
        other_accounts = [a for a in eligible if not a.is_default]

        for account in default_accounts[:1]:
            if remaining <= 0:
                break

            balance = await self._fetch_balance(account)
            balance_checks += 1
            balances_by_account[str(account.id)] = balance

            contribution = min(balance, remaining)
            if contribution >= funding_models.MIN_FUNDING_AMOUNT or contribution >= remaining:
                steps.append(account_matching.create_step(account, contribution, sequence))
                remaining -= contribution
                sequence += 1

        if remaining > 0 and len(steps) < MAX_POOLED_SOURCE_ACCOUNTS:
            for account in other_accounts[: MAX_POOLED_SOURCE_ACCOUNTS - len(steps)]:
                if remaining <= 0:
                    break

                balance = await self._fetch_balance(account)
                balance_checks += 1
                balances_by_account[str(account.id)] = balance

                contribution = min(balance, remaining)
                if contribution >= funding_models.MIN_FUNDING_AMOUNT or contribution >= remaining:
                    steps.append(account_matching.create_step(account, contribution, sequence))
                    remaining -= contribution
                    sequence += 1

        total_funded = transfer_amount - max(Decimal("0.00"), remaining)
        is_sufficient = remaining <= 0

        plan = funding_models.FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=total_funded,
            steps=steps,
            is_sufficient=is_sufficient,
            shortfall=max(Decimal("0.00"), remaining),
            balance_checks=balance_checks,
            trigger_mode="auto",
            primary_account_id=steps[0].account_id if steps else None,
            primary_bank_name=steps[0].bank_name if steps else None,
            primary_available_balance=balances_by_account.get(str(steps[0].account_id)) if steps else None,
        )

        if not is_sufficient:
            if steps:
                primary = steps[0]
                total_available = sum((s.amount for s in steps), Decimal("0.00"))
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

    async def _fetch_balance(self, account: Any) -> MoneyAmount:
        """Fetch balance for a single account."""
        if self._balance_overrides:
            override = self._balance_overrides.get(str(getattr(account, "id", "")))
            if override is not None:
                return max(Decimal("0.00"), override)
        try:
            result = await self._provider.get_balance(account.mono_account_id, real_time=True)
            return require_naira(result.available_balance) if result.success else Decimal("0.00")
        except Exception as e:
            logger.error("fetch_balance_failed", account_id=str(account.id), error=str(e))
            return Decimal("0.00")
