"""Extraction logic."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import TransferPayload
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def extract_transfer_update(
    current_payload: TransferPayload,
    extractor: Any,
    user_message: str,
    context: dict[str, Any],
) -> TransferResult:
    """Extract transfer details from user message and merge with current payload."""
    try:
        print(f"DEBUG: Extracting from '{user_message}'", flush=True)
        extraction = await extractor.extract(user_message, smart_context=context)
        print(f"DEBUG: Extraction Result: {extraction}", flush=True)

        extracted_data = {}
        if extraction.entities:
            extracted_data = extraction.entities.model_dump(exclude_unset=True, exclude_none=True)

        if extraction.correction:
            field = extraction.correction.field.value
            value = extraction.correction.new_value
            if field == "bank_name":
                extracted_data["recipient_bank_name"] = value
            else:
                extracted_data[field] = value

            print(f"DEBUG: Applied Correction: {field}={value}", flush=True)

        if not extracted_data and not extraction.acknowledgment:
            print("DEBUG: No entities or corrections found.", flush=True)
            return TransferResult(outcome=TransferOutcome.OK)

        print(f"DEBUG: Extracted Data (Pre-map): {extracted_data}", flush=True)

        # Invalidate confirmation if we have any business data update
        if extracted_data:
            extracted_data["confirmation"] = {"confirmed": False}

        if "bank_name" in extracted_data:
            extracted_data["recipient_bank_name"] = extracted_data.pop("bank_name")
        if "bank_code" in extracted_data:
            extracted_data["recipient_bank_code"] = extracted_data.pop("bank_code")

        if extraction.acknowledgment:
            extracted_data["_extraction_ack"] = extraction.acknowledgment

        if "recipient_bank_name" in extracted_data:
            extracted_data["recipient_bank_code"] = None
            extracted_data["recipient_resolved_name"] = None

        if "recipient_account" in extracted_data:
            extracted_data["recipient_resolved_name"] = None

        if "recipient_name" in extracted_data and "recipient_account" not in extracted_data:
            extracted_data["recipient_account"] = None
            extracted_data["recipient_bank_code"] = None
            extracted_data["recipient_bank_name"] = None
            extracted_data["recipient_resolved_name"] = None
            extracted_data["beneficiary_id"] = None

        return TransferResult(outcome=TransferOutcome.OK, patch=extracted_data)

    except Exception as e:
        logger.error("extraction_node_failed", error=str(e))
        return TransferResult(outcome=TransferOutcome.OK)
