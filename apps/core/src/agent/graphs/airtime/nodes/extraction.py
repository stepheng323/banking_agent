"""Airtime extraction step."""

import re
from typing import Any

from apps.core.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)


def _matches_self_airtime_phrase(message: str) -> bool:
    """Detect narrow deterministic self-airtime phrases."""
    lowered = message.lower()

    if any(token in lowered for token in ("my line", "my number", "myself", "for me")):
        return True

    return re.search(r"\bbuy me\b[\w\s]{0,80}\bairtime\b", lowered) is not None


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
        locale = context.language
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK)

        if data.skip_extraction:
            logger.info("skip_redundant_extraction", task="airtime")
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"skip_extraction": False})

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
                patch=numeric_patch,
            )

        extractor = worker_context.extractor
        if not extractor:
            logger.warning("airtime_extractor_missing")
            return TransactionResult(outcome=TransactionOutcome.OK)

        temp_state = {
            "message": self.user_message,
            "amount": data.amount,
            "recipient_phone": data.recipient_phone,
            "network": data.network,
            "recipient_name": data.recipient_name,
            "accounts": context.accounts,
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
                patch=patch,
            )

        except Exception as e:
            logger.error("airtime_extraction_failed", error=str(e))
            return TransactionResult(outcome=TransactionOutcome.OK)
