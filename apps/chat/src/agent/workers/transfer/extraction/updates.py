"""LLM extraction result merge for transfer extraction."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.transfer.extraction.parsers import (
    extract_media_caption_amount,
    extract_media_caption_narration,
    recipient_name_matches_existing_binding,
    strip_recipient_schedule_suffix,
)
from apps.chat.src.agent.workers.transfer.models.types import TransferPayload
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def extract_transfer_update(
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

        caption_narration = extract_media_caption_narration(user_message)
        if caption_narration and "narration" not in extracted_data:
            extracted_data["narration"] = caption_narration
            logger.info("transfer_extraction_caption_narration_applied")
        caption_amount = extract_media_caption_amount(user_message)
        if caption_amount is not None:
            extracted_data["amount"] = caption_amount
            logger.info("transfer_extraction_caption_amount_applied")

        if not extracted_data and not extraction.acknowledgment:
            return TransactionResult(outcome=TransactionOutcome.OK)

        extracted_percentage = "transfer_percentage" in extracted_data
        extracted_transfer_all = extracted_data.get("transfer_all") is True

        if extracted_data:
            extracted_data["confirmation"] = {"confirmed": False}
            if "amount" in extracted_data:
                extracted_data["suggested_amount"] = None
                extracted_data["transfer_percentage"] = None
                extracted_data["transfer_all"] = False
            if extracted_percentage or extracted_transfer_all:
                extracted_data["amount"] = None
                extracted_data["suggested_amount"] = None

        if "narration" in extracted_data:
            # Keep execution-facing narration while preserving the user's authored note
            # as the canonical confirmation/display value.
            extracted_data["authored_narration"] = extracted_data["narration"]
            extracted_data["user_note"] = extracted_data["narration"]

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
            extracted_data["transition_acknowledgment"] = extraction.acknowledgment

        if "recipient_bank_name" in extracted_data:
            extracted_data["recipient_bank_code"] = None
            extracted_data["recipient_bank_code_provider"] = None
            extracted_data["recipient_resolution_provider"] = None
            extracted_data["recipient_resolved_name"] = None
            extracted_data["name_mismatch"] = False
            extracted_data["name_match_score"] = None
            extracted_data["name_mismatch_warning"] = None

        if "source_bank_name" in extracted_data:
            extracted_data["source_account_id"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        if "source_account_index" in extracted_data:
            extracted_data["source_account_id"] = None
            extracted_data["source_bank_name"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        if (
            "source_accounts" in extracted_data
            or "use_dual_accounts" in extracted_data
            or "explicit_split" in extracted_data
        ):
            extracted_data["source_account_id"] = None
            extracted_data["source_bank_name"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        explicit_source_fields = {
            "source_bank_name",
            "source_account_index",
            "source_accounts",
            "use_dual_accounts",
            "explicit_split",
        }
        if any(field in extracted_data for field in explicit_source_fields):
            extracted_data["source_affinity_mode"] = "explicit"

        if "recipient_account" in extracted_data:
            extracted_data["recipient_resolved_name"] = None
            extracted_data["name_mismatch"] = False
            extracted_data["name_match_score"] = None
            extracted_data["name_mismatch_warning"] = None

        if "recipient_name" in extracted_data and "recipient_account" not in extracted_data:
            cleaned_recipient_name = strip_recipient_schedule_suffix(str(extracted_data["recipient_name"]))
            if cleaned_recipient_name:
                extracted_data["recipient_name"] = cleaned_recipient_name
            else:
                extracted_data.pop("recipient_name", None)
                extracted_data.pop("recipient_resolved_name", None)
                return TransactionResult(outcome=TransactionOutcome.OK, patch=extracted_data)

            new_name = extracted_data["recipient_name"]
            names_match = recipient_name_matches_existing_binding(new_name, current_payload)
            authoritative_fanout_binding = current_payload.recipient_binding_source == "fanout"

            if authoritative_fanout_binding and not names_match:
                extracted_data.pop("recipient_name", None)
                extracted_data.pop("recipient_resolved_name", None)
                extracted_data.pop("beneficiary_id", None)
                extracted_data.pop("resolved_from_saved_beneficiary", None)
                extracted_data.pop("name_mismatch", None)
                extracted_data.pop("name_match_score", None)
                extracted_data.pop("name_mismatch_warning", None)
                extracted_data.pop("recipient_bank_code", None)
                extracted_data.pop("recipient_bank_code_provider", None)
                extracted_data.pop("recipient_resolution_provider", None)
                extracted_data.pop("recipient_bank_name", None)

            elif not names_match:
                extracted_data["recipient_account"] = None
                extracted_data["recipient_bank_code"] = None
                extracted_data["recipient_bank_code_provider"] = None
                extracted_data["recipient_resolution_provider"] = None
                extracted_data["recipient_bank_name"] = None
                extracted_data["recipient_resolved_name"] = None
                extracted_data["beneficiary_id"] = None
                extracted_data["resolved_from_saved_beneficiary"] = False
                extracted_data["name_mismatch"] = False
                extracted_data["name_match_score"] = None
                extracted_data["name_mismatch_warning"] = None
                extracted_data["is_high_risk_transfer"] = False
                extracted_data["dynamic_risk_threshold"] = None
                extracted_data["high_risk_warning"] = None
                extracted_data["risk_advisory_reason_codes"] = []
                extracted_data["risk_advisory_score"] = 0

        funding_invalidation_fields = {
            "amount",
            "source_bank_name",
            "source_account_index",
            "source_account_id",
            "source_accounts",
            "use_dual_accounts",
            "explicit_split",
        }
        if any(field in extracted_data for field in funding_invalidation_fields):
            extracted_data["funding_plan"] = None

        return TransactionResult(outcome=TransactionOutcome.OK, patch=extracted_data)

    except Exception as error:
        logger.error("extraction_node_failed", error=str(error))
        return TransactionResult(outcome=TransactionOutcome.OK)
