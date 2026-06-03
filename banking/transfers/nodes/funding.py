"""Funding planning logic."""

from typing import Any
from uuid import UUID

from banking.presentation.i18n.personality import render_personalized_message, transfer_personality_context_from_payload
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.funding.planner import FundingPlanner
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.money import naira_to_json, require_naira
from shared.utils.logging import get_logger


class FundingStep(TransferStep):
    """Plans transaction funding (Direct Debit)."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del gates
        dd_provider = getattr(worker_context, "dd_provider", None)
        if dd_provider:
            return await plan_transaction_funding(data, context, dd_provider)
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


logger = get_logger(__name__)


def _insufficient_funds_message(payload: TransferPayload, locale: str) -> str:
    return render_personalized_message(
        "transfer.funding.insufficient_funds",
        locale,
        context=transfer_personality_context_from_payload(payload, moment="insufficient_funds"),
    )


class AccountAdapter:
    """Adapter to convert dict account data to FundingPlanner interface."""

    def __init__(self, data: dict[str, Any]):
        self.id = UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id")
        self.mono_account_id = data.get("mono_account_id") or data.get("account_id") or ""
        self.account_number = data.get("account_number", "")
        self.bank_name = data.get("bank_name", "")
        self.mandate_status = data.get("mandate_status", "pending")
        self.is_default = data.get("is_default", False)
        raw_extra = data.get("extra_data") or {}
        if isinstance(raw_extra, str):
            import json

            try:
                raw_extra = json.loads(raw_extra)
            except Exception:
                raw_extra = {}
        self.extra_data = raw_extra if isinstance(raw_extra, dict) else {}


async def plan_transaction_funding(
    payload: TransferPayload,
    ctx: TransferContext,
    dd_provider: DirectDebitProvider,
) -> TransactionResult:
    """Plan funding using shared FundingPlanner."""
    locale = ctx.language
    signature = _build_plan_signature(payload)
    existing_plan = payload.funding_plan if isinstance(payload.funding_plan, dict) else None
    if existing_plan and _signature_matches(existing_plan, signature):
        return TransactionResult(outcome=TransactionOutcome.OK)

    planner = FundingPlanner(direct_debit_provider=dd_provider)

    funding_accounts = ctx.all_accounts or ctx.accounts
    adapted_accounts = [AccountAdapter(a) for a in funding_accounts]

    amount = payload.amount or require_naira(0)
    preferred_id = None
    if payload.source_account_id:
        try:
            preferred_id = UUID(payload.source_account_id)
        except ValueError:
            preferred_id = None

    try:
        plan = await planner.plan_funding(
            accounts=adapted_accounts,
            transfer_amount=amount,
            preferred_account_id=preferred_id,
            use_dual_accounts=payload.use_dual_accounts,
            requested_source_banks=payload.source_accounts or [],
            explicit_split=payload.explicit_split or {},
            locale=locale,
        )
    except Exception as e:
        logger.error("funding_planning_failed", error=str(e))
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("transfer.funding.plan_failed", locale),
        )

    if not plan.is_sufficient:
        if plan.trigger_mode == "explicit":
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["explicit_split", "source_accounts"],
                prompt=plan.error or _insufficient_funds_message(payload, locale),
                patch={"funding_plan": None},
            )
        if plan.is_pending_mandate:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=plan.error or _insufficient_funds_message(payload, locale),
                patch={"is_pending_mandate": True, "funding_plan": None},
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["amount"],
            prompt=plan.error or _insufficient_funds_message(payload, locale),
            patch={"funding_plan": None},
        )

    plan_dict = {
        "transfer_amount": plan.transfer_amount,
        "total_funded": plan.total_funded,
        "is_sufficient": plan.is_sufficient,
        "is_single_source": plan.is_single_source,
        "trigger_mode": plan.trigger_mode,
        "requested_sources": plan.requested_sources,
        "explicit_split_applied": plan.explicit_split_applied,
        "primary_account_id": str(plan.primary_account_id) if plan.primary_account_id else None,
        "primary_bank_name": plan.primary_bank_name,
        "primary_available_balance": plan.primary_available_balance,
        "planned_for_amount": signature["planned_for_amount"],
        "planned_for_source_account_id": signature["planned_for_source_account_id"],
        "planned_for_source_accounts": signature["planned_for_source_accounts"],
        "planned_for_use_dual_accounts": signature["planned_for_use_dual_accounts"],
        "planned_for_explicit_split": signature["planned_for_explicit_split"],
        "steps": [
            {"account_id": str(s.account_id), "amount": s.amount, "bank_name": s.bank_name, "sequence": s.sequence}
            for s in plan.steps
        ],
    }

    return TransactionResult(outcome=TransactionOutcome.OK, patch={"funding_plan": plan_dict})


def _build_plan_signature(payload: TransferPayload) -> dict[str, Any]:
    explicit_split = payload.explicit_split or {}
    normalized_split = {str(k): naira_to_json(v) for k, v in sorted(explicit_split.items(), key=lambda item: item[0])}
    source_accounts = sorted([str(bank) for bank in (payload.source_accounts or []) if str(bank).strip()])
    return {
        "planned_for_amount": naira_to_json(payload.amount) or "0.00",
        "planned_for_source_account_id": payload.source_account_id,
        "planned_for_source_accounts": source_accounts,
        "planned_for_use_dual_accounts": bool(payload.use_dual_accounts),
        "planned_for_explicit_split": normalized_split,
    }


def _signature_matches(plan: dict[str, Any], signature: dict[str, Any]) -> bool:
    return all(plan.get(key) == expected for key, expected in signature.items())
