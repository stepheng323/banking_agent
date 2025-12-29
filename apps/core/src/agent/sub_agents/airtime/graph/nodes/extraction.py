"""Extraction node for airtime purchase flow."""

import time
from typing import Any, cast

from apps.core.src.agent.sub_agents.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.sub_agents.airtime.models import (
    AirtimeExtractionResult,
    SimpleAirtimeEntities,
)
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from shared.utils.logging import get_logger
from shared.utils.phone_utils import detect_network_from_phone, normalize_phone

logger = get_logger(__name__)


async def extract_entities(state: AirtimeState, extractor: AirtimeEntityExtractor) -> AirtimeState:
    """Extract entities from user message."""

    classification_result = state.get("classification_result")
    if classification_result:
        intent = classification_result.get("intent", "").lower()
        is_cancellation = intent == "cancel" or classification_result.get("is_cancellation") is True
        if is_cancellation:
            flow_state = state.get("flow_state")
            airtime_status = state.get("airtime_status")
            idempotency_key = state.get("idempotency_key")

            if (
                flow_state not in ("extracting", "error", "cancelled", None)
                or airtime_status == "pending"
                or idempotency_key
            ):
                logger.info("cancellation_detected", flow_state=flow_state)
                return {
                    **state,
                    "flow_state": "cancelled",
                    "response": "",
                }

        # Detect other interrupts - pause for other intents (will resume after)
        if intent in ("manage_accounts", "query", "transfer", "data"):
            logger.info("airtime_interrupt", intent=intent)
            return {
                **state,
                "flow_state": "paused",
                "interrupt_intent": intent,
                "response": "",
            }

    # Build smart context for extractor
    last_response = state.get("response") or state.get("llm_reply")
    smart_context = {}
    if last_response:
        smart_context["previousResponse"] = last_response

    beneficiaries = state.get("beneficiaries", [])
    from . import filter_airtime_beneficiaries

    airtime_beneficiaries = filter_airtime_beneficiaries(beneficiaries)
    if airtime_beneficiaries:
        smart_context["beneficiaries"] = airtime_beneficiaries

    language = state.get("language")
    if language:
        smart_context["language"] = language

    recent_transactions = state.get("recent_transactions", [])
    if recent_transactions:
        recent_airtime = [
            t
            for t in recent_transactions
            if t.get("type") == "airtime" and t.get("status") == "success"
        ][:3]
        if recent_airtime:
            smart_context["recentPurchases"] = recent_airtime

    pending_amount = state.get("amount")
    pending_phone = state.get("recipient_phone")
    pending_network = state.get("network")
    if pending_amount or pending_phone or pending_network:
        smart_context["pendingTransaction"] = {
            "amount": pending_amount,
            "pendingPhone": pending_phone,
            "network": pending_network,
        }

    result: AirtimeExtractionResult = await extractor.extract(
        state["message"], smart_context=smart_context if smart_context else None
    )

    entities = result.entities or SimpleAirtimeEntities()
    existing_amount = state.get("amount")
    existing_phone = state.get("recipient_phone")
    existing_network = state.get("network")

    extracted_phone = entities.recipient_phone
    extracted_network = entities.network
    extracted_amount = entities.amount
    is_self = entities.is_self

    # Handle self-recharge: use user's phone number
    user_phone = state.get("phone_number", "")
    if is_self and not extracted_phone and user_phone:
        extracted_phone = normalize_phone(user_phone) or user_phone

    normalized_phone = normalize_phone(extracted_phone) if extracted_phone else None

    # Auto-detect network from phone prefix
    detected_network = None
    if normalized_phone and not extracted_network:
        detected_network = detect_network_from_phone(normalized_phone)
        if detected_network:
            extracted_network = detected_network

    prev_phone = existing_phone
    prev_network = existing_network

    phone_changed = bool(normalized_phone and prev_phone and normalized_phone != prev_phone)
    network_changed = bool(extracted_network and prev_network and extracted_network != prev_network)
    had_prev_recipient = bool(prev_phone or prev_network)

    # Clear stale amount if recipient changed
    should_clear_amount = False
    if existing_amount is not None:
        if (phone_changed or network_changed) and had_prev_recipient:
            should_clear_amount = True

    new_state = dict(state)
    if should_clear_amount:
        new_state["amount"] = None

    updates: dict[str, Any] = {
        "missing_fields": result.missing_fields or [],
        "llm_reply": result.reply,
        "flow_state": "extracting",
    }

    # Track amount changes for acknowledgment
    amount_changed = False
    if extracted_amount is not None:
        if existing_amount is not None and existing_amount != extracted_amount:
            amount_changed = True
        updates["amount"] = extracted_amount
        updates["_amount_set_at"] = time.time()

    if amount_changed:
        updates["_amount_changed_ack"] = f"Ok, changing amount to ₦{extracted_amount:,.0f}."

    if normalized_phone:
        updates["recipient_phone"] = normalized_phone
        updates["_recipient_established_at"] = time.time()
        if not extracted_network and detected_network:
            updates["network"] = detected_network

    if extracted_network:
        updates["network"] = extracted_network
        updates["_recipient_established_at"] = time.time()

    if entities.recipient_name is not None:
        updates["recipient_name"] = entities.recipient_name

    if entities.source_account_id is not None:
        updates["source_account_id"] = entities.source_account_id

    if entities.narration is not None:
        updates["narration"] = entities.narration

    new_state.update(updates)

    # Post-update check: clear amount if recipient mismatch detected
    post_incoming_phone = normalized_phone
    post_incoming_network = extracted_network or detected_network
    post_existing_phone = new_state.get("recipient_phone")
    post_existing_network = new_state.get("network")

    is_new_phone_post = bool(post_incoming_phone and post_incoming_phone != post_existing_phone)
    is_new_network_post = bool(
        post_incoming_network and post_incoming_network != post_existing_network
    )

    if (is_new_phone_post or is_new_network_post) and new_state.get("amount") is not None:
        new_state["amount"] = None

    final_phone = new_state.get("recipient_phone")
    final_network = new_state.get("network")
    final_amount = new_state.get("amount")
    missing_fields = new_state.get("missing_fields", [])
    llm_reply = new_state.get("llm_reply")

    # Set response if all fields complete
    if (
        final_amount
        and final_phone
        and final_network
        and not missing_fields
        and llm_reply
        and not new_state.get("response")
    ):
        new_state["response"] = llm_reply

    logger.debug(
        "extract_entities_complete",
        amount=final_amount,
        phone=final_phone,
        network=final_network,
        missing=missing_fields,
    )

    return cast(AirtimeState, new_state)
