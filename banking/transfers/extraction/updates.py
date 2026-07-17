"""LLM extraction result merge for transfer extraction."""

from typing import Any

from banking.beneficiaries.services.alias_grounding import restore_exact_saved_alias
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.extraction.parsers import (
    extract_media_caption_amount,
    extract_media_caption_narration,
    recipient_name_matches_existing_binding,
    strip_recipient_schedule_suffix,
)
from banking.transfers.models.amendment import TransferAmendmentPatch
from banking.transfers.models.entities import TransferEntities
from banking.transfers.models.extraction import Correction, CorrectionField, TransferExtractionResult
from banking.transfers.models.types import TransferPayload
from shared.money import to_naira
from shared.money_mutations import AmountMutationEvaluationError, evaluate_amount_mutation
from shared.observability.llm import LLMCallDeadlineExceeded
from shared.types.amount_mutation import set_amount_mutation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def apply_transfer_amendment_update(
    current_payload: TransferPayload,
    amendment: TransferAmendmentPatch,
    *,
    user_message: str,
    context: dict[str, Any],
) -> TransactionResult:
    """Adapt the small amendment result into the existing deterministic merge path."""
    if amendment.operation != "update" or amendment.requires_broad_interpretation:
        return TransactionResult(outcome=TransactionOutcome.OK)

    entity_payload = amendment.model_dump(
        include={
            "recipient_name",
            "recipient_account",
            "source_bank_name",
            "source_account_index",
            "transfer_percentage",
            "transfer_all",
            "source_accounts",
            "use_dual_accounts",
            "explicit_split",
            "narration",
        },
        exclude_none=True,
    )
    explicit_split = entity_payload.get("explicit_split")
    if isinstance(explicit_split, list):
        entity_payload["explicit_split"] = {
            str(item.get("source")): item.get("amount")
            for item in explicit_split
            if isinstance(item, dict) and item.get("source") and item.get("amount") is not None
        }
    if amendment.recipient_bank_name:
        entity_payload["bank_name"] = amendment.recipient_bank_name
    correction = None
    if amendment.amount_mutation is not None:
        correction = Correction(
            field=CorrectionField.AMOUNT,
            amount_mutation=amendment.amount_mutation,
        )
    extraction = TransferExtractionResult(
        entities=TransferEntities.model_validate(entity_payload) if entity_payload else None,
        correction=correction,
        acknowledgment=amendment.acknowledgment,
        confirmation_intent="update",
    )

    class _BoundAmendmentExtractor:
        async def extract(self, *_args: Any, **_kwargs: Any) -> TransferExtractionResult:
            return extraction

    return await extract_transfer_update(
        current_payload,
        _BoundAmendmentExtractor(),
        user_message,
        context,
    )


def _resolved_amount_correction(current_payload: TransferPayload, correction: Any) -> object | None:
    """Return a safe absolute amount for a typed amount correction.

    The extractor identifies the user's semantic operation; arithmetic remains
    deterministic and is performed only against the amount already held in the
    pending transfer state.
    """
    mutation = getattr(correction, "amount_mutation", None)
    if mutation is None:
        legacy_amount = to_naira(correction.new_value)
        if legacy_amount is None:
            return None
        mutation = set_amount_mutation(legacy_amount)
    try:
        return float(evaluate_amount_mutation(current_payload.amount, mutation))
    except AmountMutationEvaluationError:
        return None


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
            elif field == "amount":
                resolved_amount = _resolved_amount_correction(current_payload, extraction.correction)
                extracted_data.pop("amount", None)
                if resolved_amount is not None:
                    extracted_data["amount"] = resolved_amount
                    logger.info(
                        "transfer_extraction_amount_correction_applied",
                        mutation_steps=[step.operation for step in extraction.correction.amount_mutation.steps]
                        if extraction.correction.amount_mutation
                        else ["set"],
                    )
                else:
                    logger.warning(
                        "transfer_extraction_amount_correction_rejected",
                        mutation_steps=[step.operation for step in extraction.correction.amount_mutation.steps]
                        if extraction.correction.amount_mutation
                        else ["set"],
                    )
            else:
                extracted_data[field] = value

            if field != "amount":
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

        extracted_recipient_name = extracted_data.get("recipient_name")
        if isinstance(extracted_recipient_name, str):
            beneficiaries = context.get("beneficiaries")
            grounded_alias = restore_exact_saved_alias(
                user_message,
                extracted_recipient_name,
                beneficiaries if isinstance(beneficiaries, list) else [],
            )
            if grounded_alias and grounded_alias != extracted_recipient_name:
                extracted_data["recipient_name"] = grounded_alias
                logger.info(
                    "transfer_exact_saved_alias_restored",
                    source="extractor_patch",
                    alias_token_count=len(grounded_alias.split()),
                )

        if extraction.acknowledgment:
            extracted_data["transition_acknowledgment"] = extraction.acknowledgment

        if "recipient_bank_name" in extracted_data:
            extracted_data["recipient_bank_code"] = None
            extracted_data["recipient_bank_code_provider"] = None
            extracted_data["recipient_resolution_provider"] = None
            extracted_data["recipient_resolution_mode"] = None
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
            extracted_data["recipient_resolution_mode"] = None
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
                extracted_data.pop("recipient_resolution_mode", None)
                extracted_data.pop("recipient_bank_name", None)

            elif not names_match:
                extracted_data["recipient_account"] = None
                extracted_data["recipient_bank_code"] = None
                extracted_data["recipient_bank_code_provider"] = None
                extracted_data["recipient_resolution_provider"] = None
                extracted_data["recipient_resolution_mode"] = None
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

    except LLMCallDeadlineExceeded:
        # A timeout is a recoverable control-flow outcome. The worker must
        # return localized, state-preserving recovery instead of treating it
        # as an empty extraction.
        raise
    except Exception as error:
        logger.error("extraction_node_failed", error=str(error))
        return TransactionResult(outcome=TransactionOutcome.OK)
