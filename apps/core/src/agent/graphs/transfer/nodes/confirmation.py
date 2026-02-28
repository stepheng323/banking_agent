"""Confirmation logic."""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.transfer import format_funding_plan_summary, format_transfer_summary
from shared.i18n import render_message
from shared.policy import get_cached_policy
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConfirmationStep(TransferStep):
    """Builds confirmation request and persists token."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        if gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        risk_patch = await _build_dynamic_risk_patch(data, context, worker_context)
        if risk_patch:
            data = data.model_copy(update=risk_patch)

        res = build_confirmation(data, context)
        if risk_patch:
            res.patch = {**(res.patch or {}), **risk_patch}

        try:
            redis_client = getattr(worker_context, "redis_client", None)
            key = data.idempotency_key

            if redis_client:
                # Persist tokens
                await redis_client.setex(
                    f"transfer:token:{key}:phone",
                    3600,
                    context.phone_number,
                )  # Also write generic transaction token if needed by unified handler
                await redis_client.setex(
                    f"transaction:token:{key}:phone",
                    3600,
                    context.phone_number,
                )
            else:
                logger.warning("redis_client_not_in_context_cannot_persist_transfer_token")
        except Exception as e:
            logger.error("failed_to_persist_token", error=str(e))

        return res


def _compute_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(1, math.ceil(percentile * len(sorted_values)))
    index = min(len(sorted_values) - 1, rank - 1)
    return float(sorted_values[index])


async def _build_dynamic_risk_patch(
    payload: TransferPayload,
    ctx: TransferContext,
    worker_context: Any,
) -> dict[str, Any]:
    if payload.amount is None:
        return {}

    user_id = getattr(worker_context, "user_id", None)
    tx_repo = getattr(worker_context, "transaction_repo", None)
    if not user_id or tx_repo is None:
        return {}

    policy = get_cached_policy()
    risk_cfg = policy.transfer_guardrails.dynamic_risk
    floor_amount = float(risk_cfg.floor_amount)
    lookback_days = int(risk_cfg.lookback_days)
    percentile = float(risk_cfg.percentile)

    since = datetime.now(UTC) - timedelta(days=lookback_days)
    threshold = floor_amount
    try:
        history = await tx_repo.get_successful_transfers_since(str(user_id), since)
        amounts = [float(tx.amount) for tx in history if getattr(tx, "amount", None)]
        if amounts:
            threshold = max(floor_amount, _compute_percentile(amounts, percentile))
    except Exception as exc:
        logger.warning("dynamic_risk_threshold_lookup_failed", error=str(exc))

    is_unsaved_recipient = (
        not payload.beneficiary_id and not payload.resolved_from_saved_beneficiary and not payload.is_self
    )
    amount = float(payload.amount)
    is_high_risk = bool(is_unsaved_recipient and amount >= threshold)

    warning = None
    if is_high_risk:
        warning = render_message(
            "transfer.confirmation.high_risk_unsaved_warning",
            ctx.language,
            {"amount": f"₦{amount:,.0f}", "threshold": f"₦{threshold:,.0f}"},
        )

    return {
        "dynamic_risk_threshold": threshold,
        "is_high_risk_transfer": is_high_risk,
        "high_risk_warning": warning,
    }


def build_confirmation(
    payload: TransferPayload,
    ctx: TransferContext,
) -> TransactionResult:
    """Build confirmation summary."""
    snap = {
        "amount": payload.amount,
        "recipient_name": payload.recipient_resolved_name or payload.recipient_name,
        "recipient_bank": payload.recipient_bank_name,
        "recipient_account": payload.recipient_account,
        "sourceBank": payload.source_bank_name,
        "sourceAccount": payload.source_account_number,
        "narration": payload.narration,
        "description": payload.description,
        "user_note": payload.user_note,
    }
    base_summary = format_transfer_summary(
        {
            "amount": payload.amount,
            "recipientName": payload.recipient_resolved_name or payload.recipient_name,
            "recipientBank": payload.recipient_bank_name,
            "recipientAccount": payload.recipient_account,
            "sourceBank": payload.source_bank_name,
            "sourceAccount": payload.source_account_number,
            "narration": payload.narration,
            "description": payload.description,
            "user_note": payload.user_note,
        },
        include_source=False,  # Orchestrator will handle the "From" line for batching
        locale=ctx.language,
    )
    warning_lines: list[str] = []
    if payload.name_mismatch_warning:
        warning_lines.append(payload.name_mismatch_warning)
    if payload.high_risk_warning:
        warning_lines.append(payload.high_risk_warning)
    summary = "\n\n".join([*warning_lines, base_summary]) if warning_lines else base_summary

    funding_plan = payload.funding_plan or {}
    if isinstance(funding_plan, dict) and not funding_plan.get("is_single_source", True):
        steps = funding_plan.get("steps", [])
        if isinstance(steps, list) and steps:
            primary_bank = (
                funding_plan.get("primary_bank_name")
                or steps[0].get("bank_name")
                or render_message("transfer.format.funding_plan.bank_fallback", ctx.language)
            )
            balance_val = funding_plan.get("primary_available_balance")
            primary_balance = float(balance_val if balance_val is not None else steps[0].get("amount", 0.0))
            funding_summary = format_funding_plan_summary(
                steps=steps,
                amount=float(payload.amount or funding_plan.get("transfer_amount", 0.0)),
                primary_bank=str(primary_bank),
                balance_available=primary_balance,
                recipient_name=payload.recipient_resolved_name or payload.recipient_name or "",
                recipient_bank=payload.recipient_bank_name or "",
                recipient_account=payload.recipient_account or "",
                locale=ctx.language,
            )
            summary = (
                f"{funding_summary}\n\nRecipient will be credited once after all funding debits succeed.\n\n{summary}"
            )

    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_CONFIRMATION,
        confirmation_snapshot=snap,
        confirmation_summary=summary,
    )
