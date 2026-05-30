"""Deterministic tone selection for transactional i18n copy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from banking.policy.guardrails.loader import get_cached_guardrails
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.models import LocaleCode
from banking.presentation.i18n.renderer import (
    message_key_exists,
    render_message,
)
from shared.money import MoneyAmount, to_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TransferMoment = Literal["confirmation", "success", "pending", "failure", "insufficient_funds"]
ToneVariant = Literal["standard", "warm", "careful", "trusted_careful", "reassuring", "celebratory"]

_FAILURE_MOMENTS = {"failure", "insufficient_funds"}
FREQUENT_RECIPIENT_SUCCESS_THRESHOLD = 2


@dataclass(frozen=True, slots=True)
class PersonalityContext:
    """Rendering-only context used to choose a deterministic tone variant."""

    moment: TransferMoment | None = None
    amount: MoneyAmount | None = None
    saved_recipient: bool | None = None
    frequent_recipient: bool | None = None
    high_risk: bool = False
    pooled_funding: bool = False
    dynamic_risk_threshold: float | None = None
    first_successful_transfer: bool = False
    largest_successful_transfer: bool = False
    first_pooled_success: bool = False
    recipient_success_count_90d: int | None = None


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _large_transfer_threshold() -> float:
    try:
        return float(get_cached_guardrails().transfer.dynamic_risk.floor_amount)
    except Exception as exc:
        logger.warning("personality_guardrail_threshold_unavailable", error=str(exc))
        return 50000.0


def _is_large_transfer(context: PersonalityContext) -> bool:
    amount = _coerce_optional_float(context.amount)
    if amount is None:
        return False

    threshold = context.dynamic_risk_threshold
    if threshold is None:
        threshold = _large_transfer_threshold()

    return amount >= threshold


def _is_trusted_recipient(context: PersonalityContext) -> bool:
    if context.saved_recipient or context.frequent_recipient:
        return True
    count = context.recipient_success_count_90d
    return count is not None and count >= FREQUENT_RECIPIENT_SUCCESS_THRESHOLD


def _is_large_untrusted_transfer(context: PersonalityContext) -> bool:
    return _is_large_transfer(context) and not _is_trusted_recipient(context)


def _is_success_milestone(context: PersonalityContext) -> bool:
    return bool(
        context.first_successful_transfer
        or context.largest_successful_transfer
        or context.first_pooled_success
        or context.pooled_funding
    )


def select_tone_variant(context: PersonalityContext | None) -> ToneVariant:
    """Select one deterministic tone bucket from rendering context."""
    if context is None:
        return "standard"

    if context.moment in _FAILURE_MOMENTS:
        return "reassuring"

    if context.moment == "confirmation":
        if _is_large_transfer(context) and _is_trusted_recipient(context):
            return "trusted_careful"
        if context.high_risk or _is_large_untrusted_transfer(context):
            return "careful"

    if context.moment == "success" and _is_success_milestone(context):
        return "celebratory"

    if _is_trusted_recipient(context):
        return "warm"

    return "standard"


def _variant_key(base_key: str, variant: ToneVariant) -> str:
    prefix, separator, leaf = base_key.rpartition(".")
    if not separator:
        return f"{base_key}_variants.{variant}"
    return f"{prefix}.{leaf}_variants.{variant}"


def render_personalized_message(
    base_key: MessageKey,
    locale: str | LocaleCode,
    params: dict[str, object] | None = None,
    context: PersonalityContext | None = None,
) -> str:
    """Render a tone variant with deterministic fallback to existing copy."""
    if context is None:
        return render_message(base_key, locale, params)

    selected_variant = select_tone_variant(context)
    candidate_keys = (
        _variant_key(base_key, selected_variant),
        _variant_key(base_key, "standard"),
        base_key,
    )

    for candidate_key in candidate_keys:
        if message_key_exists(candidate_key, locale):
            return render_message(cast(MessageKey, candidate_key), locale, params)

    return render_message(base_key, locale, params)


def _payload_get(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _payload_saved_recipient(payload: Any) -> bool | None:
    if bool(_payload_get(payload, "beneficiary_id")):
        return True
    saved = _payload_get(payload, "resolved_from_saved_beneficiary")
    if isinstance(saved, bool):
        return saved
    if bool(_payload_get(payload, "is_self")):
        return True
    return None


def _payload_pooled_funding(payload: Any) -> bool:
    if isinstance(_payload_get(payload, "pooled_funding"), bool):
        return bool(_payload_get(payload, "pooled_funding"))

    funding_plan = _payload_get(payload, "funding_plan")
    if isinstance(funding_plan, dict):
        return not bool(funding_plan.get("is_single_source", True))

    return False


def transfer_personality_context_from_payload(
    payload: Any,
    *,
    moment: TransferMoment | None,
) -> PersonalityContext:
    """Build transfer rendering context from existing worker/executor payload data."""
    return PersonalityContext(
        moment=moment,
        amount=to_naira(_payload_get(payload, "amount")),
        saved_recipient=_payload_saved_recipient(payload),
        high_risk=bool(_payload_get(payload, "is_high_risk_transfer")),
        pooled_funding=_payload_pooled_funding(payload),
        dynamic_risk_threshold=_coerce_optional_float(_payload_get(payload, "dynamic_risk_threshold")),
        first_successful_transfer=bool(_payload_get(payload, "first_successful_transfer")),
        largest_successful_transfer=bool(_payload_get(payload, "largest_successful_transfer")),
        first_pooled_success=bool(_payload_get(payload, "first_pooled_success")),
        recipient_success_count_90d=_coerce_optional_int(_payload_get(payload, "recipient_success_count_90d")),
    )


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
