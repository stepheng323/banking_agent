"""Extraction logic."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.database.models import Beneficiary
from shared.services.affirmation.service import AffirmationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_beneficiary_selection_from_index(
    user_message: str,
    recipient_name: str | None,
    beneficiaries_raw: list[dict[str, Any]],
) -> dict[str, Any] | None:
    clean_msg = user_message.strip()
    if not (clean_msg.isdigit() and len(clean_msg) == 1):
        return None
    if not recipient_name or not beneficiaries_raw:
        return None

    index = int(clean_msg)
    beneficiaries = [Beneficiary(**b) for b in beneficiaries_raw]
    status, _single, candidates = BeneficiaryMatcher().match(recipient_name, beneficiaries)
    if status != "clarify" or not candidates:
        return None

    selected_index = index - 1
    if selected_index < 0 or selected_index >= len(candidates):
        return None

    selected = candidates[selected_index]
    logger.info("transfer_extraction_beneficiary_numeric_fallback", index=index, beneficiary_id=str(selected.id))
    return {
        "beneficiary_id": str(selected.id),
        "confirmation": {"confirmed": False},
    }


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

        awaiting_amount = isinstance(worker_context.required_fields, list) and "amount" in worker_context.required_fields
        if awaiting_amount and data.suggested_amount:
            affirmation = AffirmationService.classify_sync(self.user_message)
            if affirmation.is_approval:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch={
                        "amount": float(data.suggested_amount),
                        "suggested_amount": None,
                        "confirmation": {"confirmed": False},
                    },
                )

        # Optimization: Phase 4 (Planner-as-Extractor)
        # Skip extraction if Planner already did it (signaled by flag)
        if data.skip_extraction:
            logger.info("skip_redundant_extraction", task="transfer")
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"skip_extraction": False})

        waiting_for_beneficiary = (
            isinstance(worker_context.required_fields, list) and "beneficiary_id" in worker_context.required_fields
        )
        if waiting_for_beneficiary:
            beneficiary_patch = _resolve_beneficiary_selection_from_index(
                self.user_message,
                data.recipient_name,
                context.beneficiaries,
            )
            if beneficiary_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=beneficiary_patch,
                )

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" to an account selection prompt, map it directly.
        numeric_patch = None if waiting_for_beneficiary else try_extract_numeric_index(self.user_message, "transfer")
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
            if "amount" in extracted_data:
                extracted_data["suggested_amount"] = None

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

        if "recipient_account" in extracted_data:
            extracted_data["recipient_resolved_name"] = None
            extracted_data["name_mismatch"] = False
            extracted_data["name_match_score"] = None
            extracted_data["name_mismatch_warning"] = None

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
                extracted_data["resolved_from_saved_beneficiary"] = False
                extracted_data["name_mismatch"] = False
                extracted_data["name_match_score"] = None
                extracted_data["name_mismatch_warning"] = None
                extracted_data["is_high_risk_transfer"] = False
                extracted_data["dynamic_risk_threshold"] = None
                extracted_data["high_risk_warning"] = None

        return TransactionResult(outcome=TransactionOutcome.OK, patch=extracted_data)

    except Exception as e:
        logger.error("extraction_node_failed", error=str(e))
        return TransactionResult(outcome=TransactionOutcome.OK)
