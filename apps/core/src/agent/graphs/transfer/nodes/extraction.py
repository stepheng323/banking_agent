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

        if not extraction.entities:
            print("DEBUG: No entities found.", flush=True)
            return TransferResult(outcome=TransferOutcome.OK)

        extracted_data = extraction.entities.model_dump(exclude_unset=True, exclude_none=True)
        print(f"DEBUG: Extracted Data (Pre-map): {extracted_data}", flush=True)

        # Remap entity keys to payload keys
        if "bank_name" in extracted_data:
            extracted_data["recipient_bank_name"] = extracted_data.pop("bank_name")
        if "bank_code" in extracted_data:
            extracted_data["recipient_bank_code"] = extracted_data.pop("bank_code")

        return TransferResult(outcome=TransferOutcome.OK, patch=extracted_data)

    except Exception as e:
        logger.error("extraction_node_failed", error=str(e))
        return TransferResult(outcome=TransferOutcome.OK)
