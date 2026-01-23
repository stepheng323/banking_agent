"""Airtime extraction step."""

from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
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
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK)

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
        }

        try:
            extracted = await extractor.run(temp_state)
            
            patch = {}
            
            if extracted.get("amount"):
                patch["amount"] = extracted["amount"]
            if extracted.get("recipient_phone"):
                patch["recipient_phone"] = extracted["recipient_phone"]
            if extracted.get("recipient_name"):
                patch["recipient_name"] = extracted["recipient_name"]
            
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
                        prompt=f"Sorry, I can't do {feature_name} airtime transfers yet. I can only do instant transfers defined right now.",
                        details={"limitation": f"{unsupported[0]}_UNSUPPORTED"}
                    )

            if extracted.get("is_self"):
                patch["is_self"] = True

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=patch,
            )

        except Exception as e:
            logger.error("airtime_extraction_failed", error=str(e))
            return TransactionResult(outcome=TransactionOutcome.OK)
