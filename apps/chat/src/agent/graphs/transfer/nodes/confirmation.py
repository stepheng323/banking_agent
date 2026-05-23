"""Confirmation logic."""

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.chat.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.currency import format_naira
from shared.formatters.recipient_display import format_recipient_display_label
from shared.formatters.transfer import format_funding_plan_summary, format_transfer_summary
from shared.guardrails.loader import get_cached_guardrails
from shared.i18n import render_message
from shared.i18n.personality import (
    PersonalityContext,
    render_personalized_message,
    transfer_personality_context_from_payload,
)
from shared.transaction_runtime.personality_enrichment import enrich_transfer_personality_context
from shared.utils.bank_aliases import normalize_bank_name
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_TRANSITION_FIELDS = ("amount", "recipient_name", "recipient_bank", "recipient_account", "narration")
_VAGUE_ACKNOWLEDGMENTS = {
    "updated",
    "updated.",
    "got it",
    "got it.",
    "alright",
    "alright.",
    "okay",
    "okay.",
    "ok",
    "ok.",
    "done",
    "done.",
}
_RECIPIENT_DISPLAY_INNER_RE = re.compile(r"^(?P<outer>[^()]+?)\s*\((?P<inner>[^()]+)\)$")
_ACK_FIELD_MARKERS: dict[str, tuple[str, ...]] = {
    "amount": ("amount", "ngn", "naira", "₦"),
    "recipient_name": ("recipient", "beneficiary"),
    "recipient_bank": ("bank",),
    "recipient_account": ("account", "acct"),
    "narration": ("narration", "memo", "note", "description"),
}


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

        personality_context = transfer_personality_context_from_payload(data, moment="confirmation")
        personality_context = await enrich_transfer_personality_context(
            personality_context,
            user_id=getattr(worker_context, "user_id", None),
            transaction_repo=getattr(worker_context, "transaction_repo", None),
            payload=data,
            idempotency_key=data.idempotency_key,
        )

        res = build_confirmation(data, context, personality_context=personality_context)
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

    is_unsaved_recipient = (
        not payload.beneficiary_id and not payload.resolved_from_saved_beneficiary and not payload.is_self
    )
    if not is_unsaved_recipient:
        return {}

    user_id = getattr(worker_context, "user_id", None)
    tx_repo = getattr(worker_context, "transaction_repo", None)
    if not user_id or tx_repo is None:
        return {}

    guardrails = get_cached_guardrails()
    risk_cfg = guardrails.transfer.dynamic_risk
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

    amount = float(payload.amount)
    is_high_risk = bool(is_unsaved_recipient and amount >= threshold)

    warning = None
    if is_high_risk:
        warning = render_personalized_message(
            "transfer.confirmation.high_risk_unsaved_warning",
            ctx.language,
            {"amount": format_naira(amount), "threshold": format_naira(threshold)},
            PersonalityContext(
                moment="confirmation",
                amount=amount,
                saved_recipient=False,
                high_risk=True,
                dynamic_risk_threshold=threshold,
            ),
        )

    return {
        "dynamic_risk_threshold": threshold,
        "is_high_risk_transfer": is_high_risk,
        "high_risk_warning": warning,
    }


def _format_naira(amount: Any) -> str | None:
    try:
        return format_naira(float(amount))
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    normalized = re.sub(r"\s+", " ", str(value)).strip().lower()
    return normalized


def _display_narration(payload: TransferPayload) -> str | None:
    for value in (payload.authored_narration, payload.user_note):
        if isinstance(value, str) and value.strip():
            return value
    return None


def _effective_narration(payload: TransferPayload) -> str | None:
    for value in (payload.authored_narration, payload.user_note, payload.narration):
        if isinstance(value, str) and value.strip():
            return value
    return None


def _derived_description(payload: TransferPayload, recipient_display_name: str | None) -> str | None:
    recipient = (recipient_display_name or payload.recipient_resolved_name or payload.recipient_name or "").strip()
    if not recipient:
        return None
    return f"Transfer to {recipient}"


def _field_value_changed(field: str, previous: Any, current: Any) -> bool:
    if field == "amount":
        try:
            return round(float(previous or 0), 2) != round(float(current or 0), 2)
        except (TypeError, ValueError):
            return _normalize_text(previous) != _normalize_text(current)
    return _normalize_text(previous) != _normalize_text(current)


def _extract_recipient_display_inner(value: Any) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    match = _RECIPIENT_DISPLAY_INNER_RE.match(normalized)
    if not match:
        return normalized
    inner = _normalize_text(match.group("inner"))
    return inner or normalized


def _same_recipient_identity(previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> bool:
    previous_account = re.sub(r"\D", "", str(previous_snapshot.get("recipient_account") or ""))
    current_account = re.sub(r"\D", "", str(current_snapshot.get("recipient_account") or ""))
    return bool(previous_account and current_account and previous_account == current_account)


def _same_recipient_display(previous: Any, current: Any) -> bool:
    previous_normalized = _normalize_text(previous)
    current_normalized = _normalize_text(current)
    if previous_normalized == current_normalized:
        return True
    previous_inner = _extract_recipient_display_inner(previous)
    current_inner = _extract_recipient_display_inner(current)
    return bool(previous_inner and current_inner and previous_inner == current_inner)


def _same_bank_identity(previous: Any, current: Any) -> bool:
    previous_normalized = _normalize_text(previous)
    current_normalized = _normalize_text(current)
    if not previous_normalized or not current_normalized:
        return False
    return normalize_bank_name(previous_normalized) == normalize_bank_name(current_normalized)


def _changed_transition_fields(previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for field in _TRANSITION_FIELDS:
        if _field_value_changed(field, previous_snapshot.get(field), current_snapshot.get(field)):
            changed.append(field)
    if _same_recipient_identity(previous_snapshot, current_snapshot):
        if "recipient_name" in changed and _same_recipient_display(
            previous_snapshot.get("recipient_name"), current_snapshot.get("recipient_name")
        ):
            changed.remove("recipient_name")
        if "recipient_bank" in changed and _same_bank_identity(
            previous_snapshot.get("recipient_bank"), current_snapshot.get("recipient_bank")
        ):
            changed.remove("recipient_bank")
    return changed


def _join_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _build_change_text(changed_fields: list[str], current_snapshot: dict[str, Any], locale: str) -> str:
    parts: list[str] = []
    if "amount" in changed_fields:
        formatted_amount = _format_naira(current_snapshot.get("amount"))
        if formatted_amount:
            parts.append(f"amount to {formatted_amount}")
    if "recipient_name" in changed_fields:
        recipient_name = str(
            current_snapshot.get("recipient_name")
            or render_message("response.common.recipient_fallback", locale)
        ).strip()
        if recipient_name:
            parts.append(f"recipient to {recipient_name}")
    if "recipient_bank" in changed_fields:
        recipient_bank = str(current_snapshot.get("recipient_bank") or "").strip()
        if recipient_bank:
            parts.append(f"bank to {recipient_bank}")
    if "recipient_account" in changed_fields:
        recipient_account = str(current_snapshot.get("recipient_account") or "").strip()
        if recipient_account:
            parts.append(f"account to {recipient_account}")
    if "narration" in changed_fields:
        narration = str(current_snapshot.get("narration") or "").strip()
        if narration:
            parts.append(f"narration to {narration}")
    if not parts:
        parts.append("your transfer details")
    return _join_parts(parts)


def _has_specific_value_reference(
    acknowledgment: str,
    *,
    changed_fields: list[str],
    current_snapshot: dict[str, Any],
) -> bool:
    normalized_ack = _normalize_text(acknowledgment)
    if not normalized_ack or normalized_ack in _VAGUE_ACKNOWLEDGMENTS:
        return False
    if _acknowledgment_mentions_unexpected_fields(normalized_ack, changed_fields=changed_fields):
        return False

    ack_digits = re.sub(r"\D", "", acknowledgment)

    for field in changed_fields:
        value = current_snapshot.get(field)
        if field == "amount":
            try:
                amount_val = float(value)
            except (TypeError, ValueError):
                continue
            formatted_amount = format_naira(amount_val).lower()
            compact_amount = str(int(round(amount_val)))
            if formatted_amount in normalized_ack:
                return True
            if compact_amount and compact_amount in ack_digits:
                return True
            if amount_val >= 1000 and amount_val % 1000 == 0:
                short_amount = f"{int(amount_val / 1000)}k"
                if short_amount in normalized_ack:
                    return True
            continue

        if field == "recipient_account":
            digits = re.sub(r"\D", "", str(value or ""))
            if digits and (digits in ack_digits or (len(digits) >= 4 and digits[-4:] in ack_digits)):
                return True
            continue

        normalized_value = _normalize_text(value)
        if not normalized_value:
            continue
        if normalized_value in normalized_ack:
            return True
        tokens = [token for token in normalized_value.split() if len(token) >= 3]
        if any(token in normalized_ack for token in tokens):
            return True
    return False


def _acknowledgment_mentions_unexpected_fields(
    normalized_ack: str,
    *,
    changed_fields: list[str],
) -> bool:
    changed = set(changed_fields)
    for field, markers in _ACK_FIELD_MARKERS.items():
        if field in changed:
            continue
        if any(marker in normalized_ack for marker in markers):
            return True
    return False


def _deterministic_transition_message(
    *,
    changed_fields: list[str],
    current_snapshot: dict[str, Any],
    locale: str,
) -> str:
    if changed_fields == ["amount"]:
        return render_message(
            "response.templates.amount_changed",
            locale,
            {"formatted_amount": _format_naira(current_snapshot.get("amount")) or ""},
        )
    if changed_fields == ["recipient_name"]:
        recipient_name = str(
            current_snapshot.get("recipient_name")
            or render_message("response.common.recipient_fallback", locale)
        ).strip()
        return render_message(
            "response.templates.recipient_changed",
            locale,
            {"recipient_name": recipient_name},
        )
    return render_message(
        "response.templates.acknowledge_change",
        locale,
        {"changes_text": _build_change_text(changed_fields, current_snapshot, locale)},
    )


def _resolve_transition_update_message(
    *,
    payload: TransferPayload,
    current_snapshot: dict[str, Any],
    locale: str,
) -> str | None:
    previous_snapshot = payload.previous_confirmation_snapshot
    if not isinstance(previous_snapshot, dict) or not previous_snapshot:
        return None

    changed_fields = _changed_transition_fields(previous_snapshot, current_snapshot)
    if not changed_fields:
        return None

    acknowledgment = (payload.transition_acknowledgment or "").strip()
    if acknowledgment and _has_specific_value_reference(
        acknowledgment,
        changed_fields=changed_fields,
        current_snapshot=current_snapshot,
    ):
        return acknowledgment

    return _deterministic_transition_message(
        changed_fields=changed_fields,
        current_snapshot=current_snapshot,
        locale=locale,
    )


def build_confirmation(
    payload: TransferPayload,
    ctx: TransferContext,
    personality_context: PersonalityContext | None = None,
) -> TransactionResult:
    """Build confirmation summary."""
    recipient_display_name = (
        format_recipient_display_label(payload.recipient_name, payload.recipient_resolved_name)
        or payload.recipient_resolved_name
        or payload.recipient_name
    )
    display_narration = _display_narration(payload)
    effective_narration = _effective_narration(payload)
    description = _derived_description(payload, recipient_display_name)
    snap = {
        "amount": payload.amount,
        "recipient_name": recipient_display_name,
        "recipient_bank": payload.recipient_bank_name,
        "recipient_account": payload.recipient_account,
        "sourceBank": payload.source_bank_name,
        "sourceAccount": payload.source_account_number,
        "authored_narration": payload.authored_narration,
        "narration": effective_narration,
        "description": description,
        "user_note": payload.user_note,
    }
    update_message = _resolve_transition_update_message(
        payload=payload,
        current_snapshot=snap,
        locale=ctx.language,
    )
    if personality_context is None:
        personality_context = transfer_personality_context_from_payload(payload, moment="confirmation")
    base_summary = format_transfer_summary(
        {
            "amount": payload.amount,
            "recipientName": recipient_display_name,
            "recipientBank": payload.recipient_bank_name,
            "recipientAccount": payload.recipient_account,
            "sourceBank": payload.source_bank_name,
            "sourceAccount": payload.source_account_number,
            "authored_narration": payload.authored_narration,
            "narration": display_narration,
            "description": description,
            "user_note": payload.user_note,
        },
        include_source=False,  # Orchestrator will handle the "From" line for batching
        locale=ctx.language,
        personality_context=personality_context,
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
            if warning_lines:
                summary = "\n\n".join(
                    [
                        *warning_lines,
                        funding_summary,
                        "Recipient will be credited once after all funding debits succeed.",
                    ]
                )
            else:
                summary = f"{funding_summary}\n\nRecipient will be credited once after all funding debits succeed."

    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_CONFIRMATION,
        confirmation_snapshot=snap,
        confirmation_summary=summary,
        update_message=update_message,
    )
