"""Extraction logic."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
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
    ) -> TransactionResult:
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        # Optimization: Phase 4 (Planner-as-Extractor)
        # Skip extraction if Planner already did it (signaled by flag)
        if data.skip_extraction:
            logger.info("skip_redundant_extraction", task="transfer")
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"skip_extraction": False})

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" to an account selection prompt, map it directly.
        numeric_patch = try_extract_numeric_index(self.user_message, "transfer")
        if numeric_patch:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=numeric_patch,
            )

        if not worker_context.extractor:
            logger.info("transfer_extraction_skipped", reason="extractor_unavailable")
            return TransactionResult(outcome=TransactionOutcome.OK)

        res = await _extract_transfer_update(
            data,
            worker_context.extractor,
            self.user_message,
            {
                "phone_number": context.phone_number,
                "language": context.language,
                "beneficiaries": context.beneficiaries,
                "accounts": context.accounts,
                "required_fields": worker_context.required_fields,
                "previousResponse": worker_context.previous_response,
                "known_recipient": {
                    "recipient_name": data.recipient_name,
                    "recipient_resolved_name": data.recipient_resolved_name,
                    "recipient_account": data.recipient_account,
                    "recipient_bank_name": data.recipient_bank_name,
                },
            },
        )

        return res


async def _extract_transfer_update(
    current_payload: TransferPayload,
    extractor: Any,
    user_message: str,
    context: dict[str, Any],
) -> TransactionResult:
    """Extract transfer details from user message and merge with current payload."""
    try:
        extraction = await extractor.extract(user_message, smart_context=context)

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

            logger.info("transfer_extraction_correction_applied", field=field)

        if not extracted_data and not extraction.acknowledgment:
            return TransactionResult(outcome=TransactionOutcome.OK)

        if extracted_data:
            extracted_data["confirmation"] = {"confirmed": False}

        # [UX] Narration vs Description Split
        # Default description to "Transfer to {name}"
        # user_note only populated if user actually typed one.
        name = (
            extracted_data.get("recipient_resolved_name")
            or extracted_data.get("recipient_name")
            or current_payload.recipient_resolved_name
            or current_payload.recipient_name
        )
        if name:
            extracted_data["description"] = f"Transfer to {name.title()}"

        if "narration" in extracted_data:
            extracted_data["user_note"] = extracted_data.pop("narration")

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
            # [FIX] Only clear account details if the name actually changed.
            # This prevents re-extraction (e.g. from proper nouns in synthesized messages)
            # from wiping out valid account details we just collected.
            new_name = extracted_data["recipient_name"]
            current_name = current_payload.recipient_name or ""

            names_match = new_name and current_name and new_name.lower().strip() == current_name.lower().strip()

            if not names_match:
                extracted_data["recipient_account"] = None
                extracted_data["recipient_bank_code"] = None
                extracted_data["recipient_bank_name"] = None
                extracted_data["recipient_resolved_name"] = None
                extracted_data["beneficiary_id"] = None

        return TransactionResult(outcome=TransactionOutcome.OK, patch=extracted_data)

    except Exception as e:
        logger.error("extraction_node_failed", error=str(e))
        return TransactionResult(outcome=TransactionOutcome.OK)
