"""Extraction node for data purchase flow - LLM-based entity extraction."""

from typing import Any

from apps.core.src.agent.graphs.data.extractor import DataEntityExtractor
from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState
from apps.core.src.agent.graphs.data.models_extraction import DataPurchaseEntities
from shared.utils.logging import get_logger
from shared.utils.phone_utils import detect_network_from_phone, normalize_phone

logger = get_logger(__name__)


async def extract_entities(
    state: DataPurchaseState,
    extractor: DataEntityExtractor,
) -> dict:
    """Extract entities from user message using LLM."""
    message = state.get("message", "")
    phone_number = state.get("phone_number", "")

    last_response = state.get("response") or state.get("llm_reply")
    smart_context: dict[str, Any] = {}
    if last_response:
        smart_context["previousResponse"] = last_response

    beneficiaries = state.get("beneficiaries", [])
    if beneficiaries:
        smart_context["beneficiaries"] = beneficiaries

    language = state.get("language")
    if language:
        smart_context["language"] = language

    result = await extractor.extract(
        message,
        smart_context=smart_context if smart_context else None,
    )

    if result.correction:
        correction = result.correction
        logger.info(
            "correction_detected",
            field=correction.field,
            old_value=correction.old_value,
            new_value=correction.new_value,
        )

    if result.ambiguities:
        logger.info("ambiguities_detected", ambiguities=result.ambiguities)

    entities = result.entities or DataPurchaseEntities()

    updates: dict[str, Any] = {
        "missing_fields": result.missing_fields or [],
        "llm_reply": result.reply,
        "flow_state": "extracting",
    }

    if entities.is_self and not entities.recipient_phone and phone_number:
        extracted_phone = normalize_phone(phone_number) or phone_number
    else:
        extracted_phone = entities.recipient_phone

    if extracted_phone:
        normalized_phone = normalize_phone(extracted_phone)
        if normalized_phone:
            updates["target_phone"] = normalized_phone
            updates["source"] = "self" if entities.is_self else "other"

            if not entities.network:
                detected_network = detect_network_from_phone(normalized_phone)
                if detected_network:
                    updates["network"] = detected_network

    if entities.network:
        updates["network"] = entities.network.upper()

    if entities.budget:
        updates["budget"] = entities.budget

    if entities.size_preference:
        updates["size_preference"] = entities.size_preference

    if entities.recipient_name:
        updates["recipient_name"] = entities.recipient_name

    logger.info(
        "extract_entities_complete",
        target_phone=updates.get("target_phone"),
        network=updates.get("network"),
        budget=updates.get("budget"),
        size_preference=updates.get("size_preference"),
        missing_fields=updates.get("missing_fields"),
    )

    return updates
