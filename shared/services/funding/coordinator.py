"""Batch funding coordinator for transfer waves."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.formatters.batch_funding import format_batch_funding_shortfall
from shared.i18n import render_message
from shared.services.funding.planner import (
    MAX_SOURCE_ACCOUNTS,
    MIN_FUNDING_AMOUNT,
    FundingPlan,
    FundingPlanner,
    FundingStepPlan,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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


class _AccountAdapter:
    def __init__(self, data: dict[str, Any]):
        raw_id = data.get("id")
        self.id = UUID(raw_id) if isinstance(raw_id, str) else raw_id
        self.mono_account_id = data.get("mono_account_id") or data.get("account_id") or ""
        self.account_number = data.get("account_number", "")
        self.bank_name = data.get("bank_name", "")
        self.mandate_id = data.get("mandate_id")
        self.mandate_status = data.get("mandate_status", "pending")
        self.is_default = bool(data.get("is_default", False))


class BatchFundingCoordinator:
    """Coordinates funding plans across a transfer wave."""

    def __init__(self, dd_provider: DirectDebitProvider):
        self._provider = dd_provider
        self._planner = FundingPlanner(direct_debit_provider=dd_provider)

    async def coordinate(
        self,
        demands: list[TransferDemand],
        accounts: list[dict[str, Any]],
        locale: str = "en",
    ) -> BatchFundingResult:
        total_demanded = sum(max(0.0, float(d.amount or 0.0)) for d in demands)
        adapted_accounts = [_AccountAdapter(acc) for acc in accounts if isinstance(acc, dict)]
        eligible_accounts = [acc for acc in adapted_accounts if acc.mandate_status == "ready" and acc.mandate_id]

        if not demands:
            return BatchFundingResult(
                is_feasible=True,
                plans_by_task={},
                total_demanded=0.0,
                total_available=0.0,
            )

        ledger = await self._fetch_balances(eligible_accounts)
        total_available = sum(ledger.values())
        accounts_by_id = {str(acc.id): acc for acc in eligible_accounts}
        bank_names_by_id = {str(acc.id): acc.bank_name for acc in eligible_accounts}

        prioritized = sorted(
            demands,
            key=lambda demand: (
                0 if demand.source_affinity.mode == "explicit" else 1,
                -float(demand.amount or 0.0),
            ),
        )

        plans_by_task: dict[str, FundingPlan] = {}
        shortfalls: list[ShortfallDetail] = []
        for demand in prioritized:
            amount = max(0.0, float(demand.amount or 0.0))
            if amount <= 0:
                plans_by_task[demand.task_id] = FundingPlan(
                    transfer_amount=0.0,
                    total_funded=0.0,
                    steps=[],
                    is_sufficient=True,
                )
                continue

            if demand.source_affinity.mode == "explicit":
                plan, shortfall = await self._allocate_explicit(
                    demand=demand,
                    ledger=ledger,
                    accounts_by_id=accounts_by_id,
                    all_accounts=eligible_accounts,
                    bank_names_by_id=bank_names_by_id,
                    locale=locale,
                )
            else:
                plan, shortfall = await self._allocate_auto(
                    demand=demand,
                    ledger=ledger,
                    accounts=eligible_accounts,
                    bank_names_by_id=bank_names_by_id,
                    locale=locale,
                )

            if plan is not None:
                plans_by_task[demand.task_id] = plan
            if shortfall is not None:
                shortfalls.append(shortfall)

        if shortfalls:
            suggestion = format_batch_funding_shortfall(
                shortfalls=shortfalls,
                total_demanded=total_demanded,
                total_available=total_available,
                locale=locale,
            )
            return BatchFundingResult(
                is_feasible=False,
                plans_by_task=plans_by_task,
                shortfalls=shortfalls,
                total_demanded=total_demanded,
                total_available=total_available,
                suggestion=suggestion,
            )

        return BatchFundingResult(
            is_feasible=True,
            plans_by_task=plans_by_task,
            shortfalls=None,
            total_demanded=total_demanded,
            total_available=total_available,
        )

    async def _allocate_auto(
        self,
        *,
        demand: TransferDemand,
        ledger: dict[str, float],
        accounts: list[_AccountAdapter],
        bank_names_by_id: dict[str, str],
        locale: str,
    ) -> tuple[FundingPlan | None, ShortfallDetail | None]:
        preferred_account_id: UUID | None = None
        if demand.preferred_account_id:
            try:
                preferred_account_id = UUID(demand.preferred_account_id)
            except ValueError:
                preferred_account_id = None

        plan = await self._planner.plan_funding(
            accounts=accounts,
            transfer_amount=float(demand.amount),
            preferred_account_id=preferred_account_id,
            locale=locale,
            balance_overrides=ledger,
        )
        if plan.is_sufficient:
            self._decrement_ledger(ledger, plan.steps)
            return plan, None

        step_account_ids = {str(step.account_id) for step in plan.steps}
        shortfall = self._build_shortfall(
            demand=demand,
            account_requested=plan.primary_bank_name or render_message("funding.format.plan.bank_fallback", locale),
            account_available=float(plan.primary_available_balance or 0.0),
            deficit=max(0.0, float(plan.shortfall or demand.amount)),
            ledger=ledger,
            exclude_account_ids=step_account_ids,
            bank_names_by_id=bank_names_by_id,
        )
        return None, shortfall

    async def _allocate_explicit(
        self,
        *,
        demand: TransferDemand,
        ledger: dict[str, float],
        accounts_by_id: dict[str, _AccountAdapter],
        all_accounts: list[_AccountAdapter],
        bank_names_by_id: dict[str, str],
        locale: str,
    ) -> tuple[FundingPlan | None, ShortfallDetail | None]:
        explicit_split = demand.explicit_split or {}
        if explicit_split:
            return self._allocate_explicit_split(
                demand=demand,
                explicit_split=explicit_split,
                ledger=ledger,
                accounts=all_accounts,
                locale=locale,
            )

        explicit_account_ids = self._resolve_explicit_account_ids(
            demand=demand,
            accounts=all_accounts,
        )
        if not explicit_account_ids and demand.preferred_account_id and demand.preferred_account_id in accounts_by_id:
            explicit_account_ids = [demand.preferred_account_id]

        if not explicit_account_ids:
            shortfall = self._build_shortfall(
                demand=demand,
                account_requested=demand.explicit_sources[0]
                if demand.explicit_sources
                else render_message("funding.format.plan.bank_fallback", locale),
                account_available=0.0,
                deficit=float(demand.amount),
                ledger=ledger,
                exclude_account_ids=set(),
                bank_names_by_id=bank_names_by_id,
            )
            return None, shortfall

        selected_ids = explicit_account_ids[:MAX_SOURCE_ACCOUNTS]
        remaining = float(demand.amount)
        sequence = 1
        steps: list[FundingStepPlan] = []
        primary_available = float(ledger.get(selected_ids[0], 0.0))
        for account_id in selected_ids:
            if remaining <= 0:
                break
            account = accounts_by_id.get(account_id)
            if account is None:
                continue
            available = max(0.0, float(ledger.get(account_id, 0.0)))
            contribution = min(available, remaining)
            if contribution > 0 and (contribution >= MIN_FUNDING_AMOUNT or contribution >= remaining):
                steps.append(
                    FundingStepPlan(
                        account_id=account.id,
                        account_number=account.account_number,
                        bank_name=account.bank_name,
                        mandate_id=account.mandate_id,
                        amount=contribution,
                        sequence=sequence,
                    )
                )
                remaining -= contribution
                sequence += 1

        if remaining > 0:
            first_account = accounts_by_id.get(selected_ids[0])
            requested_name = (
                first_account.bank_name
                if first_account is not None
                else (
                    demand.explicit_sources[0]
                    if demand.explicit_sources
                    else render_message("funding.format.plan.bank_fallback", locale)
                )
            )
            shortfall = self._build_shortfall(
                demand=demand,
                account_requested=requested_name,
                account_available=primary_available,
                deficit=remaining,
                ledger=ledger,
                exclude_account_ids=set(selected_ids),
                bank_names_by_id=bank_names_by_id,
            )
            return None, shortfall

        plan = FundingPlan(
            transfer_amount=float(demand.amount),
            total_funded=float(demand.amount),
            steps=steps,
            is_sufficient=True,
            shortfall=0.0,
            trigger_mode="explicit",
            requested_sources=list(demand.explicit_sources),
            explicit_split_applied=False,
            primary_account_id=steps[0].account_id if steps else None,
            primary_bank_name=steps[0].bank_name if steps else None,
            primary_available_balance=primary_available,
        )
        self._decrement_ledger(ledger, steps)
        return plan, None

    def _allocate_explicit_split(
        self,
        *,
        demand: TransferDemand,
        explicit_split: dict[str, float],
        ledger: dict[str, float],
        accounts: list[_AccountAdapter],
        locale: str,
    ) -> tuple[FundingPlan | None, ShortfallDetail | None]:
        if len(explicit_split) > MAX_SOURCE_ACCOUNTS:
            shortfall = self._build_shortfall(
                demand=demand,
                account_requested=next(
                    iter(explicit_split.keys()), render_message("funding.format.plan.bank_fallback", locale)
                ),
                account_available=0.0,
                deficit=float(demand.amount),
                ledger=ledger,
                exclude_account_ids=set(),
            )
            return None, shortfall

        split_total = round(sum(float(v) for v in explicit_split.values()), 2)
        if abs(split_total - float(demand.amount)) > 0.01:
            shortfall = self._build_shortfall(
                demand=demand,
                account_requested=next(
                    iter(explicit_split.keys()), render_message("funding.format.plan.bank_fallback", locale)
                ),
                account_available=0.0,
                deficit=max(0.0, float(demand.amount) - split_total),
                ledger=ledger,
                exclude_account_ids=set(),
            )
            return None, shortfall

        planned_steps: list[FundingStepPlan] = []
        used_ids: set[str] = set()
        primary_available: float | None = None
        sequence = 1
        for bank_name, requested_amount in explicit_split.items():
            account = self._match_account_by_bank_name(accounts, bank_name)
            if account is None:
                shortfall = self._build_shortfall(
                    demand=demand,
                    account_requested=bank_name,
                    account_available=0.0,
                    deficit=float(requested_amount),
                    ledger=ledger,
                    exclude_account_ids=used_ids,
                    bank_names_by_id={str(account.id): account.bank_name for account in accounts},
                )
                return None, shortfall

            account_id = str(account.id)
            if account_id in used_ids:
                shortfall = self._build_shortfall(
                    demand=demand,
                    account_requested=bank_name,
                    account_available=float(ledger.get(account_id, 0.0)),
                    deficit=float(requested_amount),
                    ledger=ledger,
                    exclude_account_ids=used_ids,
                    bank_names_by_id={str(account.id): account.bank_name for account in accounts},
                )
                return None, shortfall

            available = max(0.0, float(ledger.get(account_id, 0.0)))
            if primary_available is None:
                primary_available = available
            if available < float(requested_amount):
                shortfall = self._build_shortfall(
                    demand=demand,
                    account_requested=bank_name,
                    account_available=available,
                    deficit=max(0.0, float(requested_amount) - available),
                    ledger=ledger,
                    exclude_account_ids=used_ids | {account_id},
                    bank_names_by_id={str(account.id): account.bank_name for account in accounts},
                )
                return None, shortfall

            planned_steps.append(
                FundingStepPlan(
                    account_id=account.id,
                    account_number=account.account_number,
                    bank_name=account.bank_name,
                    mandate_id=account.mandate_id,
                    amount=float(requested_amount),
                    sequence=sequence,
                )
            )
            used_ids.add(account_id)
            sequence += 1

        self._decrement_ledger(ledger, planned_steps)
        plan = FundingPlan(
            transfer_amount=float(demand.amount),
            total_funded=float(demand.amount),
            steps=planned_steps,
            is_sufficient=True,
            shortfall=0.0,
            trigger_mode="explicit",
            requested_sources=list(explicit_split.keys()),
            explicit_split_applied=True,
            primary_account_id=planned_steps[0].account_id if planned_steps else None,
            primary_bank_name=planned_steps[0].bank_name if planned_steps else None,
            primary_available_balance=primary_available,
        )
        return plan, None

    async def _fetch_balances(self, accounts: list[_AccountAdapter]) -> dict[str, float]:
        balances: dict[str, float] = {}
        for account in accounts:
            account_id = str(account.id)
            try:
                result = await self._provider.get_balance(account.mono_account_id, real_time=True)
                balances[account_id] = float(result.available_balance) if result.success else 0.0
            except Exception as exc:
                logger.warning("batch_funding_balance_fetch_failed", account_id=account_id, error=str(exc))
                balances[account_id] = 0.0
        return balances

    def _resolve_explicit_account_ids(
        self,
        *,
        demand: TransferDemand,
        accounts: list[_AccountAdapter],
    ) -> list[str]:
        resolved: list[str] = []
        if demand.explicit_sources:
            for bank in demand.explicit_sources:
                matched = self._match_account_by_bank_name(accounts, bank)
                if matched is None:
                    continue
                account_id = str(matched.id)
                if account_id not in resolved:
                    resolved.append(account_id)
        if not resolved and demand.preferred_account_id:
            resolved.append(demand.preferred_account_id)
        return resolved

    @staticmethod
    def _match_account_by_bank_name(accounts: list[_AccountAdapter], bank_name: str) -> _AccountAdapter | None:
        target = BatchFundingCoordinator._normalize_bank_name(bank_name)
        if not target:
            return None
        for account in accounts:
            candidate = BatchFundingCoordinator._normalize_bank_name(account.bank_name)
            if target == candidate or target in candidate or candidate in target:
                return account
        return None

    @staticmethod
    def _normalize_bank_name(value: str) -> str:
        return "".join(ch for ch in str(value).lower().strip() if ch.isalnum())

    @staticmethod
    def _decrement_ledger(ledger: dict[str, float], steps: list[FundingStepPlan]) -> None:
        for step in steps:
            account_id = str(step.account_id)
            ledger[account_id] = max(0.0, float(ledger.get(account_id, 0.0)) - float(step.amount))

    def _build_shortfall(
        self,
        *,
        demand: TransferDemand,
        account_requested: str,
        account_available: float,
        deficit: float,
        ledger: dict[str, float],
        exclude_account_ids: set[str],
        bank_names_by_id: dict[str, str],
    ) -> ShortfallDetail:
        alternates = [
            {"account_id": account_id, "bank_name": bank_names_by_id.get(account_id, ""), "available": available}
            for account_id, available in ledger.items()
            if account_id not in exclude_account_ids and available > 0
        ]
        alternates = sorted(alternates, key=lambda item: float(item.get("available", 0.0)), reverse=True)
        return ShortfallDetail(
            task_id=demand.task_id,
            amount_needed=float(demand.amount),
            account_requested=account_requested,
            account_available=max(0.0, float(account_available)),
            deficit=max(0.0, float(deficit)),
            alternate_accounts=alternates[:2],
        )
