"""Extraction logic."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(TransferStep):
    """Refines transfer data from user message."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransferResult:
        if not self.user_message:
            return TransferResult(outcome=TransferOutcome.OK, patch={})

        res = await _extract_transfer_update(
            data,
            worker_context.extractor,
            self.user_message,
            {"phone_number": context.phone_number},
        )

        return res


async def _extract_transfer_update(
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

        if extracted_data:
            extracted_data["confirmation"] = {"confirmed": False}

        needs_source = (
            current_payload.recipient_account
            and current_payload.recipient_bank_name
            and not current_payload.source_account_id
        )

        if "bank_name" in extracted_data:
            if needs_source:
                extracted_data["source_bank_name"] = extracted_data.pop("bank_name")
            else:
                extracted_data["recipient_bank_name"] = extracted_data.pop("bank_name")
        if "bank_code" in extracted_data:
            extracted_data["recipient_bank_code"] = extracted_data.pop("bank_code")

        if extraction.acknowledgment:
            extracted_data["_extraction_ack"] = extraction.acknowledgment

        if "recipient_bank_name" in extracted_data:
            extracted_data["recipient_bank_code"] = None
            extracted_data["recipient_resolved_name"] = None

        if "source_bank_name" in extracted_data:
            extracted_data["source_account_id"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        if "source_account_index" in extracted_data:
            extracted_data["source_account_id"] = None
            extracted_data["source_bank_name"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

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
