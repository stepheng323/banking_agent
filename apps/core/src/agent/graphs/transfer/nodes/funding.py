"""Funding planning logic."""

from typing import Any
from uuid import UUID

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.services.funding.planner import FundingPlanner
from shared.utils.logging import get_logger


class FundingStep(TransferStep):
    """Plans transaction funding (Direct Debit)."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        dd_provider = getattr(worker_context, "dd_provider", None)
        if dd_provider:
            return await plan_transaction_funding(data, context, dd_provider)
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})



logger = get_logger(__name__)


class AccountAdapter:
    """Adapter to convert dict account data to FundingPlanner interface."""

    def __init__(self, data: dict[str, Any]):
        self.id = UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id")
        self.mono_account_id = data.get("mono_account_id", "")
        self.account_number = data.get("account_number", "")
        self.bank_name = data.get("bank_name", "")
        self.mandate_id = data.get("mandate_id")
        self.mandate_status = data.get("mandate_status", "pending")
        self.is_default = data.get("is_default", False)


async def plan_transaction_funding(
    payload: TransferPayload,
    ctx: TransferContext,
    dd_provider: DirectDebitProvider,
) -> TransactionResult:
    """Plan funding using shared FundingPlanner."""
    if payload.funding_plan:
        return TransactionResult(outcome=TransactionOutcome.OK)

    planner = FundingPlanner(direct_debit_provider=dd_provider)

    adapted_accounts = [AccountAdapter(a) for a in ctx.accounts]

    amount = payload.amount or 0.0
    preferred_id = UUID(payload.source_account_id) if payload.source_account_id else None

    try:
        plan = await planner.plan_funding(
            accounts=adapted_accounts, transfer_amount=amount, preferred_account_id=preferred_id
        )
    except Exception as e:
        logger.error("funding_planning_failed", error=str(e))
        return TransactionResult(outcome=TransactionOutcome.FAILED, error="Failed to plan funding.")

    if not plan.is_sufficient:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=plan.error or "Insufficient funds.",
        )

    plan_dict = {
        "transfer_amount": plan.transfer_amount,
        "total_funded": plan.total_funded,
        "is_sufficient": plan.is_sufficient,
        "is_single_source": plan.is_single_source,
        "steps": [
            {"account_id": str(s.account_id), "amount": s.amount, "bank_name": s.bank_name, "sequence": s.sequence}
            for s in plan.steps
        ],
    }

    return TransactionResult(outcome=TransactionOutcome.OK, patch={"funding_plan": plan_dict})
