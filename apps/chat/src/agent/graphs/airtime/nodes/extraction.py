"""Airtime extraction step."""

import re
from typing import Any

from apps.chat.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.chat.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.chat.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)
_NETWORK_CANONICAL = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_NETWORK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _matches_self_airtime_phrase(message: str) -> bool:
    """Detect narrow deterministic self-airtime phrases."""
    lowered = message.lower()

    if any(token in lowered for token in ("my line", "my number", "myself", "for me")):
        return True

    return re.search(r"\bbuy me\b[\w\s]{0,80}\bairtime\b", lowered) is not None


def _has_phone_signal(message: str) -> bool:
    return any(normalize_nigerian_phone(candidate) for candidate in _PHONE_CANDIDATE_PATTERN.findall(message))


def _has_network_signal(message: str) -> bool:
    for token in _NETWORK_TOKEN_PATTERN.findall(message):
        normalized = normalize_network_name(token)
        if normalized:
            return True
        if token.strip().upper() in _NETWORK_CANONICAL:
            return True
    return False


def _has_resolved_network(network: str | None) -> bool:
    if not network:
        return False
    normalized = normalize_network_name(network)
    if normalized:
        return True
    return network.strip().upper() in _NETWORK_CANONICAL


def _skip_override_reason(payload: AirtimePayload, message: str) -> str | None:
    normalized_phone = normalize_nigerian_phone(str(payload.recipient_phone or ""))
    if normalized_phone is None and _matches_self_airtime_phrase(message):
        return "missing_recipient_phone_with_self_signal"
    if normalized_phone is None and _has_phone_signal(message):
        return "missing_recipient_phone_with_phone_signal"
    if not _has_resolved_network(payload.network) and _has_network_signal(message):
        return "missing_network_with_network_signal"
    return None


class ExtractionStep(AirtimeStep):
    """Refines payload with extraction from current message."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates
        locale = context.language
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK)

        skip_requested = data.skip_extraction

        def _with_skip_patch(patch: dict[str, Any] | None = None) -> dict[str, Any] | None:
            if not skip_requested:
                return patch
            merged = dict(patch or {})
            merged.setdefault("skip_extraction", False)
            return merged

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" while selecting source account, map it directly.
        raw_required_fields = getattr(worker_context, "required_fields", [])
        required_fields = raw_required_fields if isinstance(raw_required_fields, list) else []
        waiting_for_source_account = "source_account_id" in required_fields
        waiting_for_recipient_phone = "recipient_phone" in required_fields or "phone_number" in required_fields
        numeric_patch = try_extract_numeric_index(self.user_message, "airtime") if waiting_for_source_account else None
        if numeric_patch:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(numeric_patch),
            )

        if skip_requested:
            override_reason = _skip_override_reason(data, self.user_message)
            if override_reason is None:
                logger.info("skip_redundant_extraction", task="airtime", reason="no_override_signal")
                return TransactionResult(outcome=TransactionOutcome.OK, patch=_with_skip_patch({}))
            logger.info("override_skip_extraction", task="airtime", reason=override_reason)

        extractor = worker_context.extractor
        if not extractor:
            logger.warning("airtime_extractor_missing")
            return TransactionResult(outcome=TransactionOutcome.OK, patch=_with_skip_patch({}))

        temp_state = {
            "message": self.user_message,
            "amount": data.amount,
            "recipient_phone": data.recipient_phone,
            "network": data.network,
            "recipient_name": data.recipient_name,
            "beneficiaries": context.beneficiaries,
            "accounts": context.accounts,
            "required_fields": required_fields,
            "previousResponse": getattr(worker_context, "previous_response", None),
            "language": context.language,
        }

        try:
            extracted = await extractor.run(temp_state)

            entities = extracted.get("entities", {})

            patch = {}

            if entities.get("amount"):
                patch["amount"] = entities["amount"]
            if entities.get("recipient_phone"):
                patch["recipient_phone"] = entities["recipient_phone"]
            if entities.get("recipient_name"):
                patch["recipient_name"] = entities["recipient_name"]
            if entities.get("network"):
                normalized_network = normalize_network_name(str(entities["network"]))
                patch["network"] = normalized_network or str(entities["network"]).strip().upper()

            if entities.get("source_account_index") is not None:
                patch["source_account_index"] = entities["source_account_index"]

            correction = extracted.get("correction")
            if correction:
                field = correction.get("field")
                value = correction.get("new_value")
                if field == "amount" and value:
                    try:
                        patch["amount"] = float(value)
                    except (ValueError, TypeError):
                        pass
                elif field == "recipient_phone" and value:
                    patch["recipient_phone"] = str(value)
                elif field == "network" and value:
                    normalized_network = normalize_network_name(str(value))
                    patch["network"] = normalized_network or str(value).strip().upper()

            # [NARROW FALLBACK]
            # If this turn is explicitly waiting for a phone number and extractor misses it,
            # accept bare-number replies deterministically.
            existing_phone = patch.get("recipient_phone") or data.recipient_phone
            if waiting_for_recipient_phone and normalize_nigerian_phone(str(existing_phone or "")) is None:
                fallback_phone = normalize_nigerian_phone(self.user_message)
                if fallback_phone:
                    patch["recipient_phone"] = fallback_phone

            # Check for unsupported features (Scheduled/Recurring)
            if extracted.get("requested_features"):
                features = extracted["requested_features"]
                # Hardcoded check: Scheduled/Recurring are not supported in V2 yet
                # We return NEEDS_INPUT with a friendly limitation message
                unsupported = [f for f in features if f in ["SCHEDULED", "RECURRING"]]
                if unsupported:
                    feature_name = unsupported[0].lower().replace("_", " ")
                    return TransactionResult(
                        outcome=TransactionOutcome.NEEDS_INPUT,
                        prompt=render_message(
                            "airtime.extraction.unsupported_feature",
                            locale,
                            {"feature_name": feature_name},
                        ),
                        details={"limitation": f"{unsupported[0]}_UNSUPPORTED"},
                        patch=_with_skip_patch({}),
                    )

            raw_is_self = entities.get("is_self")
            if raw_is_self is True:
                patch["is_self"] = True

            # [NARROW FALLBACK]
            # If extractor misses both recipient_phone and is_self on explicit
            # self-airtime phrasing, mark as self purchase.
            has_resolved_phone = bool(patch.get("recipient_phone") or data.recipient_phone)
            if raw_is_self is None and not has_resolved_phone and _matches_self_airtime_phrase(self.user_message):
                logger.info("airtime_self_fallback_applied")
                patch["is_self"] = True

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(patch),
            )

        except Exception as e:
            logger.error("airtime_extraction_failed", error=str(e))
            return TransactionResult(outcome=TransactionOutcome.OK, patch=_with_skip_patch({}))
