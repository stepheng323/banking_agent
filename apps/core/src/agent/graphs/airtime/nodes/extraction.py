"""Airtime extraction step."""

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

logger = get_logger(__name__)


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

            if entities.get("is_self"):
                patch["is_self"] = True

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=patch,
            )

        except Exception as e:
            logger.error("airtime_extraction_failed", error=str(e))
            return TransactionResult(outcome=TransactionOutcome.OK)
