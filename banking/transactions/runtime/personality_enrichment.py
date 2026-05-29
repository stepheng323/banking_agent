"""Best-effort transfer personality enrichment from transaction history."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.i18n.personality import FREQUENT_RECIPIENT_SUCCESS_THRESHOLD, PersonalityContext
from shared.utils.logging import get_logger

logger = get_logger(__name__)

RECIPIENT_FREQUENCY_LOOKBACK_DAYS = 90


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_get(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _recipient_from_payload(payload: Any) -> tuple[str | None, str | None]:
    recipient = _payload_get(payload, "recipient")
    if isinstance(recipient, dict):
        account = _clean_text(recipient.get("account_number"))
        name = _clean_text(recipient.get("name"))
        return account, name
    return _clean_text(_payload_get(payload, "recipient_account")), _clean_text(
        _payload_get(payload, "recipient_resolved_name") or _payload_get(payload, "recipient_name")
    )


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def enrich_transfer_personality_context(
    context: PersonalityContext,
    *,
    user_id: Any,
    transaction_repo: Any,
    funded_transfer_repo: Any | None = None,
    payload: Any | None = None,
    transaction_id: Any | None = None,
    idempotency_key: Any | None = None,
) -> PersonalityContext:
    """Add history-derived user moment flags without affecting transaction logic."""
    user_id_text = _clean_text(user_id)
    if not user_id_text or transaction_repo is None:
        return context

    stats_getter = getattr(transaction_repo, "get_successful_transfer_personality_stats", None)
    if not callable(stats_getter):
        return context

    try:
        recipient_account, recipient_name = _recipient_from_payload(payload)
        stats = await stats_getter(
            user_id_text,
            recipient_account_number=recipient_account,
            recipient_name=recipient_name,
            recipient_since=datetime.now(UTC) - timedelta(days=RECIPIENT_FREQUENCY_LOOKBACK_DAYS),
            exclude_transaction_id=_clean_text(transaction_id),
            exclude_idempotency_key=_clean_text(idempotency_key),
        )
        prior_success_count = _coerce_int(stats.get("prior_successful_transfer_count"))
        prior_max_amount = _coerce_float(stats.get("prior_max_successful_transfer_amount"))
        recipient_success_count = _coerce_int(stats.get("recipient_success_count_90d"))

        first_pooled_success = context.first_pooled_success
        pooled_getter = getattr(funded_transfer_repo, "has_prior_completed_pooled_transfer", None)
        if context.moment == "success" and context.pooled_funding and callable(pooled_getter):
            has_prior_pooled = await pooled_getter(
                user_id_text,
                exclude_transfer_id=None,
                exclude_idempotency_key=_clean_text(idempotency_key),
            )
            first_pooled_success = not bool(has_prior_pooled)

        amount = context.amount
        is_success = context.moment == "success"
        return replace(
            context,
            frequent_recipient=context.frequent_recipient
            or recipient_success_count >= FREQUENT_RECIPIENT_SUCCESS_THRESHOLD,
            first_successful_transfer=is_success and prior_success_count == 0,
            largest_successful_transfer=is_success and amount is not None and amount > prior_max_amount,
            first_pooled_success=first_pooled_success,
            recipient_success_count_90d=recipient_success_count,
        )
    except Exception as exc:
        logger.warning("transfer_personality_history_enrichment_failed", error=str(exc))
        return context
