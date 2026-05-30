"""Allocation helpers for batch funding coordination."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from banking.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS
from banking.presentation.i18n.renderer import render_message
from banking.transfers.funding import account_matching
from banking.transfers.funding.batch_models import (
    BatchFundingAccount,
    ShortfallDetail,
    TransferDemand,
)
from banking.transfers.funding.models import MIN_FUNDING_AMOUNT, FundingPlan, FundingStepPlan
from banking.transfers.funding.planner import FundingPlanner
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)

AllocationOutcome = tuple[FundingPlan | None, ShortfallDetail | None]


def adapt_batch_accounts(accounts: list[dict[str, Any]]) -> list[BatchFundingAccount]:
    return [BatchFundingAccount(account) for account in accounts if isinstance(account, dict)]


def eligible_batch_accounts(accounts: list[BatchFundingAccount]) -> list[BatchFundingAccount]:
    return [account for account in accounts if account.mandate_status == "ready" and account.mandate_id]


def prioritize_demands(demands: list[TransferDemand]) -> list[TransferDemand]:
    return sorted(
        demands,
        key=lambda demand: (
            0 if demand.source_affinity.mode == "explicit" else 1,
            -float(demand.amount or 0.0),
        ),
    )


async def fetch_batch_balances(
    provider: DirectDebitProvider,
    accounts: list[BatchFundingAccount],
) -> dict[str, float]:
    balances: dict[str, float] = {}
    for account in accounts:
        account_id = str(account.id)
        try:
            result = await provider.get_balance(account.mono_account_id, real_time=True)
            balances[account_id] = float(result.available_balance) if result.success else 0.0
        except Exception as exc:
            logger.warning("batch_funding_balance_fetch_failed", account_id=account_id, error=str(exc))
            balances[account_id] = 0.0
    return balances


async def allocate_auto_funding(
    *,
    demand: TransferDemand,
    ledger: dict[str, float],
    accounts: list[BatchFundingAccount],
    bank_names_by_id: dict[str, str],
    locale: str,
    planner: FundingPlanner,
) -> AllocationOutcome:
    preferred_account_id: UUID | None = None
    if demand.preferred_account_id:
        try:
            preferred_account_id = UUID(demand.preferred_account_id)
        except ValueError:
            preferred_account_id = None

    plan = await planner.plan_funding(
        accounts=accounts,
        transfer_amount=float(demand.amount),
        preferred_account_id=preferred_account_id,
        locale=locale,
        balance_overrides=ledger,
    )
    if plan.is_sufficient:
        decrement_ledger(ledger, plan.steps)
        return plan, None

    step_account_ids = {str(step.account_id) for step in plan.steps}
    shortfall = build_shortfall(
        demand=demand,
        account_requested=plan.primary_bank_name or render_message("funding.format.plan.bank_fallback", locale),
        account_available=float(plan.primary_available_balance or 0.0),
        deficit=max(0.0, float(plan.shortfall or demand.amount)),
        ledger=ledger,
        exclude_account_ids=step_account_ids,
        bank_names_by_id=bank_names_by_id,
    )
    return None, shortfall


def allocate_explicit_funding(
    *,
    demand: TransferDemand,
    ledger: dict[str, float],
    accounts_by_id: dict[str, BatchFundingAccount],
    all_accounts: list[BatchFundingAccount],
    bank_names_by_id: dict[str, str],
    locale: str,
) -> AllocationOutcome:
    explicit_split = demand.explicit_split or {}
    if explicit_split:
        return allocate_explicit_split_funding(
            demand=demand,
            explicit_split=explicit_split,
            ledger=ledger,
            accounts=all_accounts,
            bank_names_by_id=bank_names_by_id,
            locale=locale,
        )

    explicit_account_ids = resolve_explicit_account_ids(
        demand=demand,
        accounts=all_accounts,
    )
    if not explicit_account_ids and demand.preferred_account_id and demand.preferred_account_id in accounts_by_id:
        explicit_account_ids = [demand.preferred_account_id]

    if not explicit_account_ids:
        shortfall = build_shortfall(
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

    selected_ids = explicit_account_ids[:MAX_POOLED_SOURCE_ACCOUNTS]
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
        shortfall = build_shortfall(
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
    decrement_ledger(ledger, steps)
    return plan, None


def allocate_explicit_split_funding(
    *,
    demand: TransferDemand,
    explicit_split: dict[str, float],
    ledger: dict[str, float],
    accounts: list[BatchFundingAccount],
    bank_names_by_id: dict[str, str],
    locale: str,
) -> AllocationOutcome:
    requested_account_fallback = next(
        iter(explicit_split.keys()),
        render_message("funding.format.plan.bank_fallback", locale),
    )
    if len(explicit_split) > MAX_POOLED_SOURCE_ACCOUNTS:
        shortfall = build_shortfall(
            demand=demand,
            account_requested=requested_account_fallback,
            account_available=0.0,
            deficit=float(demand.amount),
            ledger=ledger,
            exclude_account_ids=set(),
            bank_names_by_id=bank_names_by_id,
        )
        return None, shortfall

    split_total = round(sum(float(value) for value in explicit_split.values()), 2)
    if abs(split_total - float(demand.amount)) > 0.01:
        shortfall = build_shortfall(
            demand=demand,
            account_requested=requested_account_fallback,
            account_available=0.0,
            deficit=max(0.0, float(demand.amount) - split_total),
            ledger=ledger,
            exclude_account_ids=set(),
            bank_names_by_id=bank_names_by_id,
        )
        return None, shortfall

    planned_steps: list[FundingStepPlan] = []
    used_ids: set[str] = set()
    primary_available: float | None = None
    sequence = 1
    for bank_name, requested_amount in explicit_split.items():
        account = account_matching.match_account_by_bank_name(accounts, bank_name)
        if account is None:
            shortfall = build_shortfall(
                demand=demand,
                account_requested=bank_name,
                account_available=0.0,
                deficit=float(requested_amount),
                ledger=ledger,
                exclude_account_ids=used_ids,
                bank_names_by_id=bank_names_by_id,
            )
            return None, shortfall

        account_id = str(account.id)
        if account_id in used_ids:
            shortfall = build_shortfall(
                demand=demand,
                account_requested=bank_name,
                account_available=float(ledger.get(account_id, 0.0)),
                deficit=float(requested_amount),
                ledger=ledger,
                exclude_account_ids=used_ids,
                bank_names_by_id=bank_names_by_id,
            )
            return None, shortfall

        available = max(0.0, float(ledger.get(account_id, 0.0)))
        if primary_available is None:
            primary_available = available
        if available < float(requested_amount):
            shortfall = build_shortfall(
                demand=demand,
                account_requested=bank_name,
                account_available=available,
                deficit=max(0.0, float(requested_amount) - available),
                ledger=ledger,
                exclude_account_ids=used_ids | {account_id},
                bank_names_by_id=bank_names_by_id,
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

    decrement_ledger(ledger, planned_steps)
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


def resolve_explicit_account_ids(
    *,
    demand: TransferDemand,
    accounts: list[BatchFundingAccount],
) -> list[str]:
    resolved: list[str] = []
    if demand.explicit_sources:
        for bank in demand.explicit_sources:
            matched = account_matching.match_account_by_bank_name(accounts, bank)
            if matched is None:
                continue
            account_id = str(matched.id)
            if account_id not in resolved:
                resolved.append(account_id)
    if not resolved and demand.preferred_account_id:
        resolved.append(demand.preferred_account_id)
    return resolved


def decrement_ledger(ledger: dict[str, float], steps: list[FundingStepPlan]) -> None:
    for step in steps:
        account_id = str(step.account_id)
        ledger[account_id] = max(0.0, float(ledger.get(account_id, 0.0)) - float(step.amount))


def build_shortfall(
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
