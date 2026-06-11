"""Batch funding coordinator for transfer waves."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from banking.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS
from banking.presentation.formatters.batch_funding import (
    format_batch_funding_approval_request,
    format_batch_funding_shortfall,
    format_batch_source_cap_shortfall,
    format_batch_source_choice_request,
)
from banking.transfers.funding.batch_allocation import (
    adapt_batch_accounts,
    allocate_auto_funding,
    allocate_explicit_funding,
    eligible_batch_accounts,
    fetch_batch_balances,
    prioritize_demands,
    resolve_explicit_account_ids,
)
from banking.transfers.funding.batch_models import (
    BatchFundingAccount,
    BatchFundingResult,
    FundingSourceChoice,
    FundingSourceOption,
    ShortfallDetail,
    TransferDemand,
)
from banking.transfers.funding.models import FundingPlan, FundingStepPlan
from banking.transfers.funding.planner import FundingPlanner
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.money import MoneyAmount, require_naira

ZERO_MONEY = Decimal("0.00")


def _source_ids_for_plan(plan: FundingPlan) -> set[str]:
    return {str(step.account_id) for step in plan.steps if require_naira(step.amount) > ZERO_MONEY}


def _accounts_within_source_cap(
    *,
    accounts: list[Any],
    ledger: dict[str, MoneyAmount],
    used_source_ids: set[str],
    selected_source_ids: list[str] | None = None,
) -> list[Any]:
    if not used_source_ids:
        return accounts

    remaining_slots = max(0, MAX_POOLED_SOURCE_ACCOUNTS - len(used_source_ids))
    selected_set = set(selected_source_ids or [])
    used_accounts = [account for account in accounts if str(account.id) in used_source_ids]
    unused_accounts = [account for account in accounts if str(account.id) not in used_source_ids]
    unused_accounts = sorted(
        unused_accounts,
        key=lambda account: (
            0 if str(account.id) in selected_set else 1,
            -require_naira(ledger.get(str(account.id), ZERO_MONEY)),
            account.bank_name.casefold(),
        ),
    )
    return used_accounts + unused_accounts[:remaining_slots]


def _capped_available_total(ledger: dict[str, MoneyAmount]) -> MoneyAmount:
    balances = sorted((max(ZERO_MONEY, require_naira(value)) for value in ledger.values()), reverse=True)
    return sum(balances[:MAX_POOLED_SOURCE_ACCOUNTS], ZERO_MONEY)


def _ordered_source_ids_for_plans(plans_by_task: dict[str, FundingPlan]) -> list[str]:
    source_ids: list[str] = []
    for plan in plans_by_task.values():
        for step in plan.steps:
            source_id = str(step.account_id)
            if source_id not in source_ids and require_naira(step.amount) > ZERO_MONEY:
                source_ids.append(source_id)
    return source_ids


def _selected_source_ids(demands: list[TransferDemand], accounts: list[BatchFundingAccount]) -> list[str]:
    selected: list[str] = []
    for demand in demands:
        has_explicit_source = (
            demand.source_affinity.mode == "explicit"
            or bool(demand.explicit_sources)
            or bool(demand.explicit_split)
            or bool(demand.use_dual_accounts)
        )
        if not has_explicit_source:
            continue
        for source_id in resolve_explicit_account_ids(demand=demand, accounts=accounts):
            if source_id not in selected:
                selected.append(source_id)
    return selected


def _anchor_source_ids(demands: list[TransferDemand], accounts: list[BatchFundingAccount]) -> list[str]:
    account_ids = {str(account.id) for account in accounts if account.id is not None}
    selected = [source_id for source_id in _selected_source_ids(demands, accounts) if source_id in account_ids]
    if selected:
        return selected[:MAX_POOLED_SOURCE_ACCOUNTS]

    preferred: list[str] = []
    for demand in demands:
        source_id = str(demand.preferred_account_id or "").strip()
        if source_id and source_id in account_ids and source_id not in preferred:
            preferred.append(source_id)
    if preferred:
        return preferred[:MAX_POOLED_SOURCE_ACCOUNTS]

    default = next((account for account in accounts if account.is_default and account.id is not None), None)
    if default is not None:
        return [str(default.id)]
    return []


def _source_options(
    *,
    accounts: list[BatchFundingAccount],
    ledger: dict[str, MoneyAmount],
    selected_source_ids: list[str],
    anchor_source_ids: list[str],
) -> list[FundingSourceOption]:
    anchor_positions = {source_id: index for index, source_id in enumerate(anchor_source_ids)}
    selected_set = set(selected_source_ids)

    options = [
        FundingSourceOption(
            account_id=str(account.id),
            bank_name=account.bank_name,
            account_number=account.account_number,
            available=max(ZERO_MONEY, require_naira(ledger.get(str(account.id), ZERO_MONEY))),
            is_default=account.is_default,
            is_selected=str(account.id) in selected_set,
        )
        for account in accounts
        if account.id is not None
    ]
    return sorted(
        options,
        key=lambda option: (
            anchor_positions.get(option.account_id, MAX_POOLED_SOURCE_ACCOUNTS + 1),
            0 if option.is_default else 1,
            -require_naira(option.available),
            option.bank_name.casefold(),
        ),
    )


def _anchored_pool_source_ids(
    *,
    source_options: list[FundingSourceOption],
    anchor_source_ids: list[str],
) -> list[str]:
    pool: list[str] = []
    available_by_id = {option.account_id: option.available for option in source_options}
    for source_id in anchor_source_ids:
        if source_id in available_by_id and source_id not in pool:
            pool.append(source_id)
        if len(pool) >= MAX_POOLED_SOURCE_ACCOUNTS:
            return pool

    remaining = [option for option in source_options if option.account_id not in pool]
    remaining = sorted(remaining, key=lambda option: require_naira(option.available), reverse=True)
    for option in remaining:
        if len(pool) >= MAX_POOLED_SOURCE_ACCOUNTS:
            break
        pool.append(option.account_id)
    return pool


def _available_for_source_ids(
    ledger: dict[str, MoneyAmount],
    source_ids: list[str],
) -> MoneyAmount:
    return sum(
        (max(ZERO_MONEY, require_naira(ledger.get(source_id, ZERO_MONEY))) for source_id in source_ids),
        ZERO_MONEY,
    )


def _can_use_aggregate_batch_planning(demands: list[TransferDemand]) -> bool:
    if len(demands) < 2:
        return False
    if any(demand.explicit_split for demand in demands):
        return False
    if any(demand.source_pooling_locked for demand in demands):
        return False
    source_contracts = {
        (
            demand.source_affinity.mode,
            tuple(demand.explicit_sources),
            bool(demand.use_dual_accounts),
            demand.preferred_account_id,
        )
        for demand in demands
    }
    if len(source_contracts) > 1:
        return False
    return True


def _accounts_by_source_id(accounts: list[BatchFundingAccount]) -> dict[str, BatchFundingAccount]:
    return {str(account.id): account for account in accounts if account.id is not None}


def _source_ids_for_anchor_plus_candidates(
    *,
    source_options: list[FundingSourceOption],
    anchor_source_ids: list[str],
    candidate_source_ids: list[str],
) -> list[str]:
    source_ids: list[str] = []
    for source_id in [*anchor_source_ids, *candidate_source_ids]:
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
        if len(source_ids) >= MAX_POOLED_SOURCE_ACCOUNTS:
            break
    option_ids = {_source_option_id(option) for option in source_options}
    return [source_id for source_id in source_ids if source_id in option_ids]


def _source_option_id(option: FundingSourceOption) -> str:
    return str(option.account_id or "").strip()


def _candidate_sources_for_remaining(
    *,
    source_options: list[FundingSourceOption],
    anchor_source_ids: list[str],
    remaining_amount: MoneyAmount,
) -> list[str]:
    anchor_ids = set(anchor_source_ids)
    return [
        option.account_id
        for option in source_options
        if option.account_id not in anchor_ids and require_naira(option.available) >= remaining_amount
    ]


def _split_aggregate_funding_plan(
    *,
    demands: list[TransferDemand],
    accounts: list[BatchFundingAccount],
    ledger: dict[str, MoneyAmount],
    source_ids: list[str],
    trigger_mode: str,
) -> dict[str, FundingPlan] | None:
    accounts_by_id = _accounts_by_source_id(accounts)
    working_ledger = dict(ledger)
    plans_by_task: dict[str, FundingPlan] = {}

    for demand in demands:
        remaining = max(ZERO_MONEY, require_naira(demand.amount))
        sequence = 1
        steps: list[FundingStepPlan] = []
        primary_available = working_ledger.get(source_ids[0], ZERO_MONEY) if source_ids else ZERO_MONEY
        for source_id in source_ids:
            if remaining <= ZERO_MONEY:
                break
            account = accounts_by_id.get(source_id)
            if account is None or account.id is None:
                continue
            available = max(ZERO_MONEY, require_naira(working_ledger.get(source_id, ZERO_MONEY)))
            contribution = min(available, remaining)
            if contribution <= ZERO_MONEY:
                continue
            steps.append(
                FundingStepPlan(
                    account_id=account.id,
                    account_number=account.account_number,
                    bank_name=account.bank_name,
                    amount=contribution,
                    sequence=sequence,
                )
            )
            working_ledger[source_id] = max(ZERO_MONEY, available - contribution)
            remaining -= contribution
            sequence += 1

        amount = max(ZERO_MONEY, require_naira(demand.amount))
        if remaining > ZERO_MONEY:
            return None
        plans_by_task[demand.task_id] = FundingPlan(
            transfer_amount=amount,
            total_funded=amount,
            steps=steps,
            is_sufficient=True,
            shortfall=ZERO_MONEY,
            trigger_mode=trigger_mode,  # type: ignore[arg-type]
            requested_sources=[
                accounts_by_id[source_id].bank_name
                for source_id in source_ids
                if source_id in accounts_by_id
            ],
            explicit_split_applied=False,
            primary_account_id=steps[0].account_id if steps else None,
            primary_bank_name=steps[0].bank_name if steps else None,
            primary_available_balance=primary_available,
        )

    return plans_by_task


def _aggregate_batch_plan_result(
    *,
    demands: list[TransferDemand],
    accounts: list[BatchFundingAccount],
    ledger: dict[str, MoneyAmount],
    total_demanded: MoneyAmount,
    total_available: MoneyAmount,
    source_options: list[FundingSourceOption],
    anchor_source_ids: list[str],
    selected_source_ids: list[str],
    locale: str,
) -> BatchFundingResult | None:
    if not _can_use_aggregate_batch_planning(demands) or not anchor_source_ids:
        return None

    selected_set = set(selected_source_ids)
    explicit_source_requested = bool(selected_set) or any(
        demand.source_affinity.mode == "explicit" or demand.explicit_sources or demand.use_dual_accounts
        for demand in demands
    )

    anchor_available = _available_for_source_ids(ledger, anchor_source_ids)
    selected_available = _available_for_source_ids(ledger, selected_source_ids)
    if selected_source_ids and len(selected_source_ids) >= 2:
        source_ids = selected_source_ids[:MAX_POOLED_SOURCE_ACCOUNTS]
        if selected_available >= total_demanded:
            plans = _split_aggregate_funding_plan(
                demands=demands,
                accounts=accounts,
                ledger=ledger,
                source_ids=source_ids,
                trigger_mode="explicit",
            )
            if plans is not None:
                return BatchFundingResult(
                    is_feasible=True,
                    plans_by_task=plans,
                    total_demanded=total_demanded,
                    total_available=total_available,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    suggested_source_ids=_ordered_source_ids_for_plans(plans),
                )

    if anchor_available >= total_demanded:
        plans = _split_aggregate_funding_plan(
            demands=demands,
            accounts=accounts,
            ledger=ledger,
            source_ids=anchor_source_ids[:MAX_POOLED_SOURCE_ACCOUNTS],
            trigger_mode="explicit" if explicit_source_requested else "auto",
        )
        if plans is not None:
            return BatchFundingResult(
                is_feasible=True,
                plans_by_task=plans,
                total_demanded=total_demanded,
                total_available=total_available,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                suggested_source_ids=_ordered_source_ids_for_plans(plans),
            )

    remaining_amount = max(ZERO_MONEY, total_demanded - anchor_available)
    candidate_source_ids = _candidate_sources_for_remaining(
        source_options=source_options,
        anchor_source_ids=anchor_source_ids,
        remaining_amount=remaining_amount,
    )
    if remaining_amount > ZERO_MONEY and len(anchor_source_ids) < MAX_POOLED_SOURCE_ACCOUNTS:
        if len(candidate_source_ids) > 1 and not explicit_source_requested:
            source_choice = FundingSourceChoice(
                remaining_amount=remaining_amount,
                candidate_source_ids=candidate_source_ids,
            )
            suggestion = format_batch_source_choice_request(
                total_demanded=total_demanded,
                remaining_amount=remaining_amount,
                candidate_source_ids=candidate_source_ids,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                locale=locale,
            )
            return BatchFundingResult(
                is_feasible=False,
                total_demanded=total_demanded,
                total_available=total_available,
                suggestion=suggestion,
                requires_source_choice=True,
                source_choice=source_choice,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                suggested_source_ids=anchor_source_ids,
            )
        if len(candidate_source_ids) > 1 and explicit_source_requested:
            source_choice = FundingSourceChoice(
                remaining_amount=remaining_amount,
                candidate_source_ids=candidate_source_ids,
            )
            suggestion = format_batch_source_choice_request(
                total_demanded=total_demanded,
                remaining_amount=remaining_amount,
                candidate_source_ids=candidate_source_ids,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                locale=locale,
            )
            return BatchFundingResult(
                is_feasible=False,
                total_demanded=total_demanded,
                total_available=total_available,
                suggestion=suggestion,
                requires_source_choice=True,
                source_choice=source_choice,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                suggested_source_ids=anchor_source_ids,
            )
        if len(candidate_source_ids) == 1:
            source_ids = _source_ids_for_anchor_plus_candidates(
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                candidate_source_ids=candidate_source_ids,
            )
            plans = _split_aggregate_funding_plan(
                demands=demands,
                accounts=accounts,
                ledger=ledger,
                source_ids=source_ids,
                trigger_mode="auto",
            )
            if plans is not None:
                suggestion = format_batch_funding_approval_request(
                    plans_by_task=plans,
                    total_demanded=total_demanded,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    suggested_source_ids=source_ids,
                    locale=locale,
                )
                return BatchFundingResult(
                    is_feasible=False,
                    suggested_plans_by_task=plans,
                    total_demanded=total_demanded,
                    total_available=total_available,
                    suggestion=suggestion,
                    requires_user_approval=True,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    suggested_source_ids=source_ids,
                )

    pool_source_ids = _anchored_pool_source_ids(
        source_options=source_options,
        anchor_source_ids=anchor_source_ids,
    )
    capped_available = _available_for_source_ids(ledger, pool_source_ids)
    best_capped_available = _capped_available_total(ledger)
    if total_available >= total_demanded and (
        capped_available < total_demanded or best_capped_available < total_demanded
    ):
        suggestion = format_batch_source_cap_shortfall(
            total_demanded=total_demanded,
            capped_available=capped_available,
            max_source_accounts=MAX_POOLED_SOURCE_ACCOUNTS,
            source_options=source_options,
            anchor_source_ids=anchor_source_ids,
            pool_source_ids=pool_source_ids,
            locale=locale,
        )
        return BatchFundingResult(
            is_feasible=False,
            shortfalls=[],
            total_demanded=total_demanded,
            total_available=total_available,
            suggestion=suggestion,
            source_options=source_options,
            anchor_source_ids=anchor_source_ids,
            suggested_source_ids=pool_source_ids,
            capped_available=capped_available,
            funding_shortfall=max(ZERO_MONEY, total_demanded - capped_available),
        )

    return None


def _implicit_source_choice_needed(
    *,
    demands: list[TransferDemand],
    plans_by_task: dict[str, FundingPlan],
    source_options: list[FundingSourceOption],
    anchor_source_ids: list[str],
) -> FundingSourceChoice | None:
    if not anchor_source_ids:
        return None
    if any(demand.source_affinity.mode == "explicit" for demand in demands):
        return None
    if any(demand.explicit_sources or demand.explicit_split or demand.use_dual_accounts for demand in demands):
        return None

    suggested_source_ids = _ordered_source_ids_for_plans(plans_by_task)
    added_source_ids = [source_id for source_id in suggested_source_ids if source_id not in set(anchor_source_ids)]
    if len(added_source_ids) != 1:
        return None

    added_source_id = added_source_ids[0]
    remaining_amount = sum(
        (
            require_naira(step.amount)
            for plan in plans_by_task.values()
            for step in plan.steps
            if str(step.account_id) == added_source_id
        ),
        ZERO_MONEY,
    )
    if remaining_amount <= ZERO_MONEY:
        return None

    by_id = {option.account_id: option for option in source_options}
    candidates = [
        option.account_id
        for option in source_options
        if option.account_id not in set(anchor_source_ids) and require_naira(option.available) >= remaining_amount
    ]
    if len(candidates) <= 1:
        return None
    if added_source_id not in by_id:
        return None
    return FundingSourceChoice(remaining_amount=remaining_amount, candidate_source_ids=candidates)


def _plan_needs_user_approval(demand: TransferDemand, plan: FundingPlan) -> bool:
    if demand.source_affinity.mode == "explicit":
        return False
    plan_source_ids = _source_ids_for_plan(plan)
    if len(plan_source_ids) > 1:
        return True
    return bool(
        demand.preferred_account_id and any(source_id != demand.preferred_account_id for source_id in plan_source_ids)
    )


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
        total_demanded = sum(
            (max(ZERO_MONEY, require_naira(demand.amount)) for demand in demands),
            ZERO_MONEY,
        )
        eligible_accounts = eligible_batch_accounts(adapt_batch_accounts(accounts))

        if not demands:
            return BatchFundingResult(
                is_feasible=True,
                plans_by_task={},
                total_demanded=ZERO_MONEY,
                total_available=ZERO_MONEY,
            )

        ledger = await fetch_batch_balances(self._provider, eligible_accounts)
        initial_ledger = dict(ledger)
        total_available: MoneyAmount = sum(ledger.values(), ZERO_MONEY)
        bank_names_by_id = {str(account.id): account.bank_name for account in eligible_accounts}
        selected_source_ids = _selected_source_ids(demands, eligible_accounts)
        anchor_source_ids = _anchor_source_ids(demands, eligible_accounts)
        source_options = _source_options(
            accounts=eligible_accounts,
            ledger=initial_ledger,
            selected_source_ids=selected_source_ids,
            anchor_source_ids=anchor_source_ids,
        )
        aggregate_result = _aggregate_batch_plan_result(
            demands=demands,
            accounts=eligible_accounts,
            ledger=initial_ledger,
            total_demanded=total_demanded,
            total_available=total_available,
            source_options=source_options,
            anchor_source_ids=anchor_source_ids,
            selected_source_ids=selected_source_ids,
            locale=locale,
        )
        if aggregate_result is not None:
            return aggregate_result

        plans_by_task: dict[str, FundingPlan] = {}
        shortfalls: list[ShortfallDetail] = []
        used_source_ids: set[str] = set()
        requires_user_approval = False
        for demand in prioritize_demands(demands):
            amount = max(ZERO_MONEY, require_naira(demand.amount))
            if amount <= 0:
                plans_by_task[demand.task_id] = FundingPlan(
                    transfer_amount=ZERO_MONEY,
                    total_funded=ZERO_MONEY,
                    steps=[],
                    is_sufficient=True,
                )
                continue

            candidate_accounts = _accounts_within_source_cap(
                accounts=eligible_accounts,
                ledger=ledger,
                used_source_ids=used_source_ids,
                selected_source_ids=selected_source_ids,
            )
            candidate_accounts_by_id = {str(account.id): account for account in candidate_accounts}
            if demand.source_affinity.mode == "explicit":
                plan, shortfall = allocate_explicit_funding(
                    demand=demand,
                    ledger=ledger,
                    accounts_by_id=candidate_accounts_by_id,
                    all_accounts=candidate_accounts,
                    bank_names_by_id=bank_names_by_id,
                    locale=locale,
                )
            else:
                plan, shortfall = await allocate_auto_funding(
                    demand=demand,
                    ledger=ledger,
                    accounts=candidate_accounts,
                    bank_names_by_id=bank_names_by_id,
                    locale=locale,
                    planner=self._planner,
                )

            if plan is not None:
                plan_source_ids = _source_ids_for_plan(plan)
                if len(used_source_ids | plan_source_ids) > MAX_POOLED_SOURCE_ACCOUNTS:
                    shortfall = ShortfallDetail(
                        task_id=demand.task_id,
                        amount_needed=require_naira(demand.amount),
                        account_requested="pooled sources",
                        account_available=sum(
                            (require_naira(ledger.get(source_id, ZERO_MONEY)) for source_id in used_source_ids),
                            ZERO_MONEY,
                        ),
                        deficit=require_naira(demand.amount),
                    )
                    plan = None
                else:
                    if _plan_needs_user_approval(demand, plan):
                        requires_user_approval = True
                    used_source_ids.update(plan_source_ids)
                    plans_by_task[demand.task_id] = plan
                    plan = None
            if plan is not None:
                plans_by_task[demand.task_id] = plan
            if shortfall is not None:
                shortfalls.append(shortfall)

        if shortfalls:
            pool_source_ids = _anchored_pool_source_ids(
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
            )
            capped_available = _available_for_source_ids(initial_ledger, pool_source_ids)
            best_capped_available = _capped_available_total(initial_ledger)
            if total_available >= total_demanded and (
                capped_available < total_demanded or best_capped_available < total_demanded
            ):
                suggestion = format_batch_source_cap_shortfall(
                    total_demanded=total_demanded,
                    capped_available=capped_available,
                    max_source_accounts=MAX_POOLED_SOURCE_ACCOUNTS,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    pool_source_ids=pool_source_ids,
                    locale=locale,
                )
            else:
                suggestion = format_batch_funding_shortfall(
                    shortfalls=shortfalls,
                    total_demanded=total_demanded,
                    total_available=total_available,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    locale=locale,
                )
            return BatchFundingResult(
                is_feasible=False,
                plans_by_task=plans_by_task,
                shortfalls=shortfalls,
                total_demanded=total_demanded,
                total_available=total_available,
                suggestion=suggestion,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                suggested_source_ids=pool_source_ids,
                capped_available=capped_available,
                funding_shortfall=max(ZERO_MONEY, total_demanded - capped_available),
            )

        if requires_user_approval:
            suggested_source_ids = _ordered_source_ids_for_plans(plans_by_task)
            source_choice = _implicit_source_choice_needed(
                demands=demands,
                plans_by_task=plans_by_task,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
            )
            if source_choice is not None:
                suggestion = format_batch_source_choice_request(
                    total_demanded=total_demanded,
                    remaining_amount=source_choice.remaining_amount,
                    candidate_source_ids=source_choice.candidate_source_ids,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    locale=locale,
                )
                return BatchFundingResult(
                    is_feasible=False,
                    plans_by_task={},
                    suggested_plans_by_task={},
                    shortfalls=None,
                    total_demanded=total_demanded,
                    total_available=total_available,
                    suggestion=suggestion,
                    requires_source_choice=True,
                    source_choice=source_choice,
                    source_options=source_options,
                    anchor_source_ids=anchor_source_ids,
                    suggested_source_ids=suggested_source_ids,
                )

            suggestion = format_batch_funding_approval_request(
                plans_by_task=plans_by_task,
                total_demanded=total_demanded,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                suggested_source_ids=suggested_source_ids,
                locale=locale,
            )
            return BatchFundingResult(
                is_feasible=False,
                plans_by_task={},
                suggested_plans_by_task=plans_by_task,
                shortfalls=None,
                total_demanded=total_demanded,
                total_available=total_available,
                suggestion=suggestion,
                requires_user_approval=True,
                source_options=source_options,
                anchor_source_ids=anchor_source_ids,
                suggested_source_ids=suggested_source_ids,
            )

        return BatchFundingResult(
            is_feasible=True,
            plans_by_task=plans_by_task,
            shortfalls=None,
            total_demanded=total_demanded,
            total_available=total_available,
            source_options=source_options,
            anchor_source_ids=anchor_source_ids,
            suggested_source_ids=_ordered_source_ids_for_plans(plans_by_task),
        )
