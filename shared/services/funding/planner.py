"""Deterministic funding planner for multi-account transfers.

100% deterministic logic for financial safety.

Strategy: "Lazy Balance Fetching"
- Only fetch balances when needed (minimize API costs)
- If user specifies source account, use only that account
- Otherwise: try default first, then add more if needed
"""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from shared.clients.abstractions import DirectDebitProvider
from shared.formatters.currency import format_naira
from shared.formatters.funding import (
    format_insufficient_funds,
)
from shared.i18n import render_message
from shared.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.bank_aliases import normalize_bank_name
from shared.utils.logging import get_logger

logger = get_logger(__name__)

MAX_SOURCE_ACCOUNTS = MAX_POOLED_SOURCE_ACCOUNTS
MIN_FUNDING_AMOUNT = 100.0


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
    trigger_mode: Literal["auto", "explicit"] = "auto"
    requested_sources: list[str] = field(default_factory=list)
    explicit_split_applied: bool = False
    primary_account_id: UUID | None = None
    primary_bank_name: str | None = None
    primary_available_balance: float | None = None

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
        self._balance_overrides: dict[str, float] | None = None

    async def plan_funding(
        self,
        accounts: list[Any],
        transfer_amount: float,
        preferred_account_id: UUID | None = None,
        use_dual_accounts: bool | None = None,
        requested_source_banks: list[str] | None = None,
        explicit_split: dict[str, float] | None = None,
        locale: str = "en",
        balance_overrides: dict[str, float] | None = None,
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
            use_dual_accounts=bool(use_dual_accounts),
            requested_source_banks=requested_source_banks or [],
            has_explicit_split=bool(explicit_split),
            has_balance_overrides=bool(balance_overrides),
        )

        old_overrides = self._balance_overrides
        self._balance_overrides = dict(balance_overrides or {}) or None
        try:
            eligible = [a for a in accounts if self._is_eligible(a)]

            if not eligible:
                # Distinguish between "pending mandate" and "no mandate at all"
                pending_accounts = [a for a in accounts if getattr(a, "mandate_status", None) not in (None, "ready")]
                if pending_accounts:
                    error_msg = self._build_pending_mandate_message(pending_accounts[0], locale)
                else:
                    error_msg = render_message("funding.planner.no_active_mandates", locale)
                return FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=0,
                    is_sufficient=False,
                    error=error_msg,
                    is_pending_mandate=bool(pending_accounts),
                )

            normalized_requested_sources = [
                s for s in (requested_source_banks or []) if isinstance(s, str) and s.strip()
            ]
            cleaned_explicit_split = {
                bank: float(amount)
                for bank, amount in (explicit_split or {}).items()
                if isinstance(bank, str) and bank.strip() and isinstance(amount, (int, float)) and float(amount) > 0
            }

            # Case 1: Explicit pooling request takes precedence
            if cleaned_explicit_split or use_dual_accounts or normalized_requested_sources:
                return await self._plan_explicit_pooling(
                    eligible=eligible,
                    all_accounts=accounts,
                    transfer_amount=transfer_amount,
                    requested_source_banks=normalized_requested_sources,
                    explicit_split=cleaned_explicit_split,
                    force_multi_source=bool(use_dual_accounts or normalized_requested_sources),
                    locale=locale,
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

    async def _plan_explicit_pooling(
        self,
        *,
        eligible: list[Any],
        all_accounts: list[Any],
        transfer_amount: float,
        requested_source_banks: list[str],
        explicit_split: dict[str, float],
        force_multi_source: bool,
        locale: str,
    ) -> FundingPlan:
        requested_accounts: list[Any] = []
        if requested_source_banks:
            for bank_name in requested_source_banks:
                account = self._match_account_by_bank_name(eligible, bank_name)
                if account and all(str(existing.id) != str(account.id) for existing in requested_accounts):
                    requested_accounts.append(account)
                    continue

                ineligible = self._match_ineligible_requested_account(all_accounts, eligible, bank_name)
                if ineligible is not None:
                    return FundingPlan(
                        transfer_amount=transfer_amount,
                        total_funded=0,
                        is_sufficient=False,
                        trigger_mode="explicit",
                        requested_sources=requested_source_banks,
                        error=self._build_explicit_nonready_account_message(ineligible, locale),
                    )

            if not requested_accounts:
                return FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=0,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    requested_sources=requested_source_banks,
                    error=render_message(
                        "funding.planner.preferred_account_not_eligible",
                        locale,
                    ),
                )

        candidates = requested_accounts or eligible

        if explicit_split:
            return await self._plan_with_explicit_split(
                candidates=candidates,
                all_accounts=all_accounts,
                transfer_amount=transfer_amount,
                explicit_split=explicit_split,
                locale=locale,
                requested_source_banks=requested_source_banks,
            )

        candidates = candidates[:MAX_SOURCE_ACCOUNTS]
        steps: list[FundingStepPlan] = []
        remaining = transfer_amount
        balance_checks = 0
        sequence = 1
        balances: list[tuple[Any, float]] = []
        for account in candidates:
            available = await self._fetch_balance(account)
            balance_checks += 1
            balances.append((account, available))

        if force_multi_source and len(balances) >= 2 and transfer_amount > 0:
            first_account, first_balance = balances[0]
            second_account, second_balance = balances[1]
            first_target = transfer_amount / 2
            first_contribution = min(first_balance, first_target)
            second_contribution = min(second_balance, transfer_amount - first_contribution)
            remaining_after_two = transfer_amount - (first_contribution + second_contribution)

            if remaining_after_two > 0 and first_balance > first_contribution:
                extra = min(first_balance - first_contribution, remaining_after_two)
                first_contribution += extra
                remaining_after_two -= extra

            if first_contribution > 0:
                steps.append(self._create_step(first_account, first_contribution, sequence))
                sequence += 1
            if second_contribution > 0:
                steps.append(self._create_step(second_account, second_contribution, sequence))
                sequence += 1
            remaining = remaining_after_two

        for account, balance in balances:
            if remaining <= 0 or len(steps) >= MAX_SOURCE_ACCOUNTS:
                break
            if any(str(existing.account_id) == str(account.id) for existing in steps):
                continue
            contribution = min(balance, remaining)
            if contribution > 0:
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
            trigger_mode="explicit",
            requested_sources=requested_source_banks,
            explicit_split_applied=False,
            primary_account_id=steps[0].account_id if steps else None,
            primary_bank_name=steps[0].bank_name if steps else None,
            primary_available_balance=balances[0][1] if balances else None,
        )
        if not is_sufficient:
            plan.error = format_insufficient_funds(
                transfer_amount=transfer_amount,
                bank_name=steps[0].bank_name if steps else render_message("funding.format.plan.bank_fallback", locale),
                available_balance=steps[0].amount if steps else 0.0,
                max_available=total_funded,
                locale=locale,
            )
        return plan

    async def _plan_with_explicit_split(
        self,
        *,
        candidates: list[Any],
        all_accounts: list[Any],
        transfer_amount: float,
        explicit_split: dict[str, float],
        locale: str,
        requested_source_banks: list[str],
    ) -> FundingPlan:
        if len(explicit_split) > MAX_SOURCE_ACCOUNTS:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                trigger_mode="explicit",
                requested_sources=requested_source_banks,
                explicit_split_applied=True,
                error=("Please use at most 2 source accounts in your split. Revise the split and try again."),
            )

        split_total = round(sum(explicit_split.values()), 2)
        if abs(split_total - transfer_amount) > 0.01:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                trigger_mode="explicit",
                requested_sources=requested_source_banks,
                explicit_split_applied=True,
                error=(
                    "Your split does not match the transfer amount. "
                    f"Requested total: {format_naira(split_total)}, transfer amount: {format_naira(transfer_amount)}. "
                    "Please revise the split."
                ),
            )

        steps: list[FundingStepPlan] = []
        balance_checks = 0
        sequence = 1
        total_funded = 0.0
        used_account_ids: set[str] = set()
        primary_available_balance: float | None = None
        for requested_bank, requested_amount in explicit_split.items():
            account = self._match_account_by_bank_name(candidates, requested_bank)
            if account is None:
                ineligible = self._match_ineligible_requested_account(all_accounts, candidates, requested_bank)
                if ineligible is not None:
                    return FundingPlan(
                        transfer_amount=transfer_amount,
                        total_funded=total_funded,
                        is_sufficient=False,
                        trigger_mode="explicit",
                        requested_sources=requested_source_banks,
                        explicit_split_applied=True,
                        error=self._build_explicit_nonready_account_message(ineligible, locale),
                    )
                return FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=total_funded,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    requested_sources=requested_source_banks,
                    explicit_split_applied=True,
                    error=(
                        f"I could not match '{requested_bank}' to your eligible linked accounts. "
                        "Please revise the split."
                    ),
                )

            account_id = str(account.id)
            if account_id in used_account_ids:
                return FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=total_funded,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    requested_sources=requested_source_banks,
                    explicit_split_applied=True,
                    error="Please use distinct source accounts in your split and try again.",
                )
            used_account_ids.add(account_id)

            available = await self._fetch_balance(account)
            balance_checks += 1
            if primary_available_balance is None:
                primary_available_balance = available
            if available < requested_amount:
                return FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=total_funded,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    requested_sources=requested_source_banks,
                    explicit_split_applied=True,
                    error=(
                        f"Your split is not feasible: {account.bank_name} has {format_naira(available)}, "
                        f"but you requested {format_naira(requested_amount)}. Please revise the split."
                    ),
                )

            steps.append(self._create_step(account, requested_amount, sequence))
            total_funded += requested_amount
            sequence += 1

        return FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=total_funded,
            steps=steps,
            is_sufficient=True,
            shortfall=0.0,
            balance_checks=balance_checks,
            trigger_mode="explicit",
            requested_sources=requested_source_banks,
            explicit_split_applied=True,
            primary_account_id=steps[0].account_id if steps else None,
            primary_bank_name=steps[0].bank_name if steps else None,
            primary_available_balance=primary_available_balance,
        )

    async def _plan_with_preferred_account(
        self,
        eligible: list[Any],
        all_accounts: list[Any],
        transfer_amount: float,
        preferred_account_id: UUID,
        locale: str,
    ) -> FundingPlan:
        """Plan using preferred account first, then pool if needed."""
        account = next((a for a in eligible if a.id == preferred_account_id), None)

        if not account:
            pending_match = next((a for a in all_accounts if getattr(a, "id", None) == preferred_account_id), None)
            if pending_match is not None and not self._is_eligible(pending_match):
                return FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=0,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    error=self._build_explicit_nonready_account_message(pending_match, locale),
                )
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                error=render_message("funding.planner.preferred_account_not_eligible", locale),
            )

        balance = await self._fetch_balance(account)
        primary_balance = balance

        if balance >= transfer_amount:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=transfer_amount,
                steps=[self._create_step(account, transfer_amount, 1)],
                is_sufficient=True,
                balance_checks=1,
                trigger_mode="auto",
                primary_account_id=account.id,
                primary_bank_name=account.bank_name,
                primary_available_balance=primary_balance,
            )

        steps = [self._create_step(account, balance, 1)] if balance > 0 else []
        remaining = max(0.0, transfer_amount - balance)
        balance_checks = 1
        sequence = 2

        others = [candidate for candidate in eligible if candidate.id != preferred_account_id]
        for candidate in others[: max(0, MAX_SOURCE_ACCOUNTS - len(steps))]:
            if remaining <= 0:
                break
            available = await self._fetch_balance(candidate)
            balance_checks += 1
            contribution = min(available, remaining)
            if contribution > 0:
                steps.append(self._create_step(candidate, contribution, sequence))
                remaining -= contribution
                sequence += 1

        total_funded = transfer_amount - max(0, remaining)
        plan = FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=total_funded,
            steps=steps,
            is_sufficient=remaining <= 0,
            shortfall=max(0, remaining),
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
        transfer_amount: float,
        locale: str,
    ) -> FundingPlan:
        """Plan with lazy balance fetching - default first, then largest."""
        steps: list[FundingStepPlan] = []
        remaining = transfer_amount
        balance_checks = 0
        sequence = 1
        balances_by_account: dict[str, float] = {}

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
                balances_by_account[str(account.id)] = balance

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
            trigger_mode="auto",
            primary_account_id=steps[0].account_id if steps else None,
            primary_bank_name=steps[0].bank_name if steps else None,
            primary_available_balance=balances_by_account.get(str(steps[0].account_id)) if steps else None,
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

    def _match_account_by_bank_name(self, accounts: list[Any], bank_name: str) -> Any | None:
        target = self._normalize_bank_name(bank_name)
        if not target:
            return None
        for account in accounts:
            candidate_name = self._normalize_bank_name(getattr(account, "bank_name", "") or "")
            if target == candidate_name or target in candidate_name or candidate_name in target:
                return account
        return None

    @staticmethod
    def _normalize_bank_name(value: str) -> str:
        return "".join(ch for ch in value.lower().strip() if ch.isalnum())

    async def _fetch_balance(self, account: Any) -> float:
        """Fetch balance for a single account."""
        if self._balance_overrides:
            override = self._balance_overrides.get(str(getattr(account, "id", "")))
            if override is not None:
                return max(0.0, float(override))
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

    def _match_ineligible_requested_account(self, all_accounts: list[Any], eligible: list[Any], bank_name: str) -> Any | None:
        eligible_ids = {str(getattr(account, "id", "")) for account in eligible}
        normalized_request = normalize_bank_name(bank_name)
        for account in all_accounts:
            if str(getattr(account, "id", "")) in eligible_ids:
                continue
            account_bank = str(getattr(account, "bank_name", "") or "")
            normalized_bank = normalize_bank_name(account_bank)
            if (
                normalized_request
                and (
                    normalized_request in normalized_bank
                    or normalized_bank in normalized_request
                    or normalized_request.replace(" ", "") in normalized_bank.replace(" ", "")
                )
            ):
                return account
        return None

    def _build_explicit_nonready_account_message(self, account: Any, locale: str) -> str:
        account_dict = {
            "bank_name": getattr(account, "bank_name", ""),
            "account_number": getattr(account, "account_number", ""),
            "mandate_status": getattr(account, "mandate_status", ""),
            "extra_data": getattr(account, "extra_data", {}) or {},
        }
        bank_name = str(account_dict.get("bank_name") or render_message("mandate.bank_fallback", locale))
        pending_message = build_pending_mandate_message([account_dict], locale)
        return f"Your {bank_name} account is linked, but it is not ready for payments yet.\n\n{pending_message}"

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
            svc = MandateService(queue=None)
            return svc.build_mandate_auth_message(
                account_number=account_number,
                bank_name=bank_name,
                transfer_destinations=destinations,
            )

        return render_message("mandate.pending_complete_transfer", locale)
