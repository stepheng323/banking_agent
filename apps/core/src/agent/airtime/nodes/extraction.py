"""Extraction node for airtime purchase flow."""

import re
import time
from typing import Any, cast

from apps.core.src.agent.airtime.state import AirtimeState
from apps.core.src.agent.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.models.airtime_extraction import AirtimeExtractionResult, SimpleAirtimeEntities
from ..graph.utils import debug_log


def normalize_phone(phone: str) -> str:
    """Normalize phone number by removing spaces, dashes, and country code if present."""
    if not phone:
        return ""

    normalized = re.sub(r'[\s\-_]', '', phone)
    if normalized.startswith('234') and len(normalized) == 13:
        normalized = normalized[3:]
    elif normalized.startswith('+234'):
        normalized = normalized[4:]
    elif normalized.startswith('00234'):
        normalized = normalized[5:]
    return normalized.strip()


def detect_network_from_phone(phone: str) -> str | None:
    """Detect network from phone number prefix."""
    if not phone:
        return None

    normalized = normalize_phone(phone)
    if len(normalized) < 10:
        return None

    prefix = normalized[:4]

    if prefix in ['0803', '0806', '0703', '0706', '0813', '0816', '0810', '0814', '0903', '0906']:
        return 'MTN'
    elif prefix in ['0802', '0808', '0708', '0812', '0901', '0902', '0904', '0907']:
        return 'Airtel'
    elif prefix in ['0805', '0807', '0705', '0815', '0811', '0905']:
        return 'Glo'
    elif prefix in ['0809', '0817', '0818', '0908', '0909']:
        return '9mobile'

    return None


async def extract_entities(state: AirtimeState, extractor: AirtimeEntityExtractor) -> AirtimeState:
    """Extract entities from user message."""

    classification_result = state.get("classification_result")
    if classification_result:
        is_cancellation = classification_result.get("intent", "").lower(
        ) == "cancel" or classification_result.get("is_cancellation") is True
        if is_cancellation:
            flow_state = state.get("flow_state")
            airtime_status = state.get("airtime_status")
            idempotency_key = state.get("idempotency_key")

            if (flow_state not in ("extracting", "error", "cancelled", None) or
                airtime_status == "pending" or
                    idempotency_key):
                debug_log(
                    f"🚫 Cancellation detected via classification result: flow_state={flow_state}, airtime_status={airtime_status}, idempotency_key={idempotency_key}")
                return {
                    **state,
                    "flow_state": "cancelled",
                    "response": "",  # Will be set in handle_cancellation
                }
    last_response = state.get("response") or state.get("llm_reply")
    smart_context = {}
    if last_response:
        smart_context["previousResponse"] = last_response

    # Include airtime beneficiaries in context to help extractor distinguish aliases
    beneficiaries = state.get("beneficiaries", [])
    airtime_beneficiaries = []
    for b in beneficiaries:
        if isinstance(b, dict):
            if b.get("beneficiary_type") == "airtime":
                airtime_beneficiaries.append(b)
        elif hasattr(b, "beneficiary_type") and b.beneficiary_type == "airtime":
            airtime_beneficiaries.append(b)
    if airtime_beneficiaries:
        smart_context["beneficiaries"] = airtime_beneficiaries

    result: AirtimeExtractionResult = await extractor.extract(state["message"], smart_context=smart_context if smart_context else None)

    entities = result.entities or SimpleAirtimeEntities()
    existing_amount = state.get("amount")
    existing_phone = state.get("recipient_phone")
    existing_network = state.get("network")

    extracted_phone = entities.recipient_phone
    extracted_network = entities.network
    extracted_amount = entities.amount

    normalized_phone = None
    if extracted_phone:
        normalized_phone = normalize_phone(extracted_phone)

    detected_network = None
    if normalized_phone and not extracted_network:
        detected_network = detect_network_from_phone(normalized_phone)
        if detected_network:
            extracted_network = detected_network

    prev_phone = existing_phone
    prev_network = existing_network

    phone_changed_pre_update = bool(
        normalized_phone and prev_phone and normalized_phone != prev_phone
    )
    network_changed_pre_update = bool(
        extracted_network and prev_network and extracted_network != prev_network
    )
    recipient_changed_pre_update = phone_changed_pre_update or network_changed_pre_update
    had_prev_recipient = bool(prev_phone or prev_network)

    debug_log(
        f"DEBUG extract_entities: Pre-update compare - prev_phone={prev_phone}, prev_network={prev_network}, "
        f"incoming_phone={normalized_phone}, incoming_network={extracted_network}, "
        f"phone_changed_pre_update={phone_changed_pre_update}, network_changed_pre_update={network_changed_pre_update}, "
        f"recipient_changed_pre_update={recipient_changed_pre_update}")

    # Check if this is a new airtime purchase intent
    classification_intent = (
        state.get("classification_result") or {}).get("intent", "")
    is_new_airtime_intent = classification_intent.lower() in {
        "airtime", "buy airtime", "purchase airtime", "buy data", "purchase data"
    }
    recipient_arrives_this_turn = bool(normalized_phone or extracted_network)

    # Decide if we should clear stale amount
    should_clear_amount_pre_update = False
    if existing_amount is not None:
        if (phone_changed_pre_update or network_changed_pre_update) and had_prev_recipient:
            should_clear_amount_pre_update = True
            debug_log(
                "ℹ️ extract_entities: Recipient changed (pre-update) with prior recipient. Will clear stale amount.")

    debug_log(
        f"DEBUG extract_entities: Pre-update amount clearing decision - existing_amount={existing_amount}, "
        f"is_new_airtime_intent={is_new_airtime_intent}, recipient_arrives_this_turn={recipient_arrives_this_turn}, "
        f"should_clear_amount_pre_update={should_clear_amount_pre_update}")

    # Do NOT clear recipient when only amount arrives
    should_clear_stale_recipient = False
    if (extracted_amount is not None and
        not normalized_phone and
        not extracted_network and
            (existing_phone or existing_network)):
        debug_log(
            "ℹ️ extract_entities: Amount provided without new recipient; preserving existing phone/network.")

    new_state = dict(state)

    if should_clear_amount_pre_update:
        new_state["amount"] = None

    if should_clear_stale_recipient:
        debug_log(
            "🧹 CLEARING stale recipient data - setting phone and network to None")
        new_state = {
            **new_state,
            "recipient_phone": None,
            "network": None,
            "validation_errors": [],
        }

    updates: dict[str, Any] = {
        "missing_fields": result.missing_fields or [],
        "llm_reply": result.reply,
        "flow_state": "extracting",
    }

    if extracted_amount is not None:
        updates["amount"] = extracted_amount
        updates["_amount_set_at"] = time.time()
    elif existing_amount:
        pass

    if normalized_phone:
        updates["recipient_phone"] = normalized_phone
        updates["_recipient_established_at"] = time.time()
        if not extracted_network and detected_network:
            updates["network"] = detected_network
            debug_log(
                f"ℹ️ extract_entities: Auto-detected network '{detected_network}' from phone number")
    elif existing_phone:
        pass

    if extracted_network:
        updates["network"] = extracted_network
        if not normalized_phone and existing_phone:
            debug_log(
                f"ℹ️ extract_entities: Preserving existing phone '{existing_phone}' when user provided network")
        updates["_recipient_established_at"] = time.time()
    elif existing_network:
        pass

    if entities.recipient_name is not None:
        updates["recipient_name"] = entities.recipient_name

    if entities.source_account_id is not None:
        updates["source_account_id"] = entities.source_account_id

    if entities.narration is not None:
        updates["narration"] = entities.narration

    new_state.update(updates)

    post_incoming_phone = normalized_phone
    post_incoming_network = extracted_network or detected_network
    post_existing_phone = new_state.get("recipient_phone")
    post_existing_network = new_state.get("network")

    is_new_phone_post = bool(
        post_incoming_phone and post_incoming_phone != post_existing_phone)
    is_new_network_post = bool(
        post_incoming_network and post_incoming_network != post_existing_network)

    if (is_new_phone_post or is_new_network_post) and new_state.get("amount") is not None:
        debug_log(
            "ℹ️ extract_entities: Post-update detected recipient mismatch -> clearing previous amount")
        new_state["amount"] = None

    debug_log(
        f"DEBUG extract_entities: Timestamps - _recipient_established_at={new_state.get('_recipient_established_at')}, _amount_set_at={new_state.get('_amount_set_at')}")

    final_phone = new_state.get("recipient_phone")
    final_network = new_state.get("network")
    final_amount = new_state.get("amount")
    missing_fields = new_state.get("missing_fields", [])
    llm_reply = new_state.get("llm_reply")

    debug_log(
        f"✅ extract_entities FINAL STATE: recipient_phone={final_phone}, network={final_network}, amount={final_amount}, missing_fields={missing_fields}")

    if (final_amount and final_phone and final_network and
        not missing_fields and llm_reply and
            not new_state.get("response")):
        new_state["response"] = llm_reply
        debug_log(
            "✅ extract_entities: All fields complete, setting response from llm_reply")

    if should_clear_stale_recipient and final_phone:
        debug_log(
            f"❌ ERROR: Stale data clearing failed! recipient_phone should be None but is {final_phone}")
        new_state["recipient_phone"] = None
        new_state["network"] = None

    return cast(AirtimeState, new_state)
