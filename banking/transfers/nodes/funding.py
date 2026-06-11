"""Funding planning logic."""

from typing import Any
from uuid import UUID

from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.personality import render_personalized_message, transfer_personality_context_from_payload
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.funding.plan_validation import (
    build_funding_plan_signature,
    funding_adjustment_details,
    funding_plan_matches_signature,
)
from banking.transfers.funding.planner import FundingPlanner
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.money import require_naira
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


def _last4(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else "????"


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


def _funding_plan_dict(plan: Any, signature: dict[str, Any]) -> dict[str, Any]:
    return {
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
            {
                "account_id": str(s.account_id),
                "account_number": s.account_number,
                "amount": s.amount,
                "bank_name": s.bank_name,
                "sequence": s.sequence,
            }
            for s in plan.steps
        ],
    }


def _implicit_pooled_plan_needs_approval(payload: TransferPayload, plan: Any) -> bool:
    return bool(
        plan.is_multi_source
        and plan.trigger_mode == "auto"
        and payload.source_affinity_mode != "explicit"
        and not payload.use_dual_accounts
        and not payload.source_accounts
        and not payload.explicit_split
    )


def _single_transfer_funding_approval_prompt(payload: TransferPayload, plan: Any) -> str:
    amount = payload.amount or plan.transfer_amount
    lines = ["Funding review", "", f"This transfer needs {format_naira(amount)}."]
    primary_bank = plan.primary_bank_name or (plan.steps[0].bank_name if plan.steps else None)
    primary_balance = plan.primary_available_balance
    if primary_bank and primary_balance is not None:
        label = "selected" if payload.source_affinity_mode == "explicit" else "default"
        lines.append(f"Your {label} {primary_bank} has {format_naira(primary_balance)}.")

    extra_banks = [step.bank_name for step in plan.steps[1:] if step.bank_name]
    lines.append("")
    if extra_banks:
        lines.append(f"I can suggest this breakdown and add {' and '.join(extra_banks)} to complete it:")
    else:
        lines.append("I can suggest this breakdown:")
    lines.append("")
    for step in plan.steps:
        lines.append(f"* {step.bank_name} (···{_last4(step.account_number)}): {format_naira(step.amount)}")
    lines.extend(
        [
            "",
            "Reply yes to use this breakdown, or tell me a different source or amount.",
        ]
    )
    return "\n".join(lines).strip()


async def plan_transaction_funding(
    payload: TransferPayload,
    ctx: TransferContext,
    dd_provider: DirectDebitProvider,
) -> TransactionResult:
    """Plan funding using shared FundingPlanner."""
    locale = ctx.language
    signature = build_funding_plan_signature(payload)
    existing_plan = payload.funding_plan if isinstance(payload.funding_plan, dict) else None
    if existing_plan and funding_plan_matches_signature(existing_plan, signature):
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
                details=funding_adjustment_details("insufficient"),
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
            details=funding_adjustment_details("insufficient"),
            patch={"funding_plan": None},
        )

    plan_dict = _funding_plan_dict(plan, signature)

    if _implicit_pooled_plan_needs_approval(payload, plan):
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["suggested_funding_plan", "amount", "source_accounts", "explicit_split"],
            prompt=_single_transfer_funding_approval_prompt(payload, plan),
            details=funding_adjustment_details("suggested_pooling"),
            patch={"suggested_funding_plan": plan_dict, "funding_plan": None},
        )

    return TransactionResult(outcome=TransactionOutcome.OK, patch={"funding_plan": plan_dict})
