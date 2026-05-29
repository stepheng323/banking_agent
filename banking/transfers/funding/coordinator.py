"""Batch funding coordinator for transfer waves."""

from __future__ import annotations

from typing import Any

from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.formatters.batch_funding import format_batch_funding_shortfall
from banking.transfers.funding.batch_allocation import (
    adapt_batch_accounts,
    allocate_auto_funding,
    allocate_explicit_funding,
    eligible_batch_accounts,
    fetch_batch_balances,
    prioritize_demands,
)
from banking.transfers.funding.batch_models import BatchFundingResult, ShortfallDetail, TransferDemand
from banking.transfers.funding.models import FundingPlan
from banking.transfers.funding.planner import FundingPlanner


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
        total_demanded = sum(max(0.0, float(demand.amount or 0.0)) for demand in demands)
        eligible_accounts = eligible_batch_accounts(adapt_batch_accounts(accounts))

        if not demands:
            return BatchFundingResult(
                is_feasible=True,
                plans_by_task={},
                total_demanded=0.0,
                total_available=0.0,
            )

        ledger = await fetch_batch_balances(self._provider, eligible_accounts)
        total_available = sum(ledger.values())
        accounts_by_id = {str(account.id): account for account in eligible_accounts}
        bank_names_by_id = {str(account.id): account.bank_name for account in eligible_accounts}

        plans_by_task: dict[str, FundingPlan] = {}
        shortfalls: list[ShortfallDetail] = []
        for demand in prioritize_demands(demands):
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
                plan, shortfall = allocate_explicit_funding(
                    demand=demand,
                    ledger=ledger,
                    accounts_by_id=accounts_by_id,
                    all_accounts=eligible_accounts,
                    bank_names_by_id=bank_names_by_id,
                    locale=locale,
                )
            else:
                plan, shortfall = await allocate_auto_funding(
                    demand=demand,
                    ledger=ledger,
                    accounts=eligible_accounts,
                    bank_names_by_id=bank_names_by_id,
                    locale=locale,
                    planner=self._planner,
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
