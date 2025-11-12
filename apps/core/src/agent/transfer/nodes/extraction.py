"""Entity extraction node for transfer flow."""

from typing import Any, cast

from apps.core.src.agent.models.transfer import SimpleTransferEntities
from apps.core.src.agent.models.transfer_extraction import TransferExtractionResult
from apps.core.src.agent.services.transfer_entity_extractor import TransferEntityExtractor
from apps.core.src.agent.transfer.state import TransferState

from .utils import debug_log


async def extract_entities(
    state: TransferState,
    extractor: TransferEntityExtractor,
) -> TransferState:
    """Extract entities from user message."""

    classification_result = state.get("classification_result")
    if classification_result:
        is_cancellation = (
            classification_result.get("intent", "").lower() == "cancel" or
            classification_result.get("is_cancellation") is True
        )
        if is_cancellation:
            flow_state = state.get("flow_state")
            transfer_status = state.get("transfer_status")
            idem_key = state.get("idempotency_key")

            if (flow_state not in ("extracting", "error", "cancelled", None) or
                transfer_status == "pending" or
                    idem_key):
                debug_log(
                    f"🚫 Cancellation detected via classification result: flow_state={flow_state}, transfer_status={transfer_status}, idem_key={idem_key}")
                return {
                    **state,
                    "flow_state": "cancelled",
                    "response": "",  # Will be set in handle_cancellation
                }

    last_response = state.get("response") or state.get("llm_reply")
    smart_context = None
    if last_response:
        smart_context = {"previousResponse": last_response}

    result: TransferExtractionResult = await extractor.extract(state["message"], smart_context=smart_context)

    entities = result.entities or SimpleTransferEntities()
    existing_amount = state.get("amount")

    debug_log(
        f"DEBUG extract_entities: Raw extraction - account='{entities.recipient_account}', bank_name='{entities.bank_name}', bank_code='{entities.bank_code}', amount='{entities.amount}'")

    existing_recipient_in_state = state.get("recipient_account")
    existing_bank_in_state = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")
    debug_log(
        f"DEBUG extract_entities: State before processing - existing recipient_account={existing_recipient_in_state}, bank={existing_bank_in_state}")

    should_clear_stale_recipient = False
    current_flow_state = state.get("flow_state")
    transfer_status = state.get("transfer_status")

    if (entities.amount is not None and
        entities.recipient_account is None and
        entities.recipient_name is None and
        current_flow_state != "collecting_recipient" and
            transfer_status != "pending"):
        if existing_recipient_in_state or existing_bank_in_state:
            should_clear_stale_recipient = True
            debug_log(
                f"🔴 CRITICAL: extract_entities detected stale recipient data! amount={entities.amount}, existing_recipient={existing_recipient_in_state}, existing_bank={existing_bank_in_state}, flow_state={current_flow_state}")

    new_state = dict(state)

    if should_clear_stale_recipient:
        debug_log(
            "🧹 CLEARING stale recipient data - setting all recipient fields to None")
        new_state = {
            **new_state,
            "recipient_account": None,
            "recipient_bank_code": None,
            "recipient_bank_name": None,
            "recipient_name": None,
            "account_resolved": None,
            "matched_beneficiary": None,
            "validation_errors": [],
            # Clear narration when clearing stale recipient data (new transfer)
            "narration": None,
        }
        debug_log(
            f"✅ AFTER CLEARING: recipient_account={new_state.get('recipient_account')}, recipient_bank={new_state.get('recipient_bank_code')}")

    updates: dict[str, Any] = {
        "missing_fields": result.missingFields or [],
        "llm_reply": result.reply,
        "flow_state": "extracting",
    }

    if entities.amount is not None:
        updates["amount"] = entities.amount
    elif existing_amount:
        pass
    if entities.recipient_name is not None:
        updates["recipient_name"] = entities.recipient_name

    if entities.recipient_account is not None:
        normalized_account = str(entities.recipient_account).replace(
            " ", "").replace("-", "").replace("_", "").strip()
        updates["recipient_account"] = normalized_account
        debug_log(
            f"DEBUG extract_entities: Normalized account '{entities.recipient_account}' -> '{normalized_account}'")
        if entities.bank_name is None and entities.bank_code is None:
            existing_bank_code = new_state.get("recipient_bank_code")
            existing_bank_name = new_state.get("recipient_bank_name")
            if existing_bank_code or existing_bank_name:
                debug_log(
                    f"ℹ️ extract_entities: Preserving existing bank name '{existing_bank_name}' when user provided account number")

    if entities.bank_code is not None:
        updates["recipient_bank_code"] = entities.bank_code
    if entities.bank_name is not None:
        updates["recipient_bank_name"] = entities.bank_name
        debug_log(
            f"DEBUG extract_entities: Extracted bank_name: '{entities.bank_name}'")
        if entities.recipient_account is None:
            existing_account = new_state.get("recipient_account")
            if existing_account:
                debug_log(
                    f"ℹ️ extract_entities: Preserving existing account number '{existing_account}' when user provided bank name")

    if entities.source_account_id is not None:
        updates["source_account_id"] = entities.source_account_id
    if entities.narration is not None:
        updates["narration"] = entities.narration

    new_state.update(updates)

    final_recipient = new_state.get("recipient_account")
    final_bank = new_state.get(
        "recipient_bank_code") or new_state.get("recipient_bank_name")
    final_amount = new_state.get("amount")
    missing_fields = new_state.get("missing_fields", [])
    llm_reply = new_state.get("llm_reply")

    debug_log(
        f"✅ extract_entities FINAL STATE: recipient_account={final_recipient}, recipient_bank={final_bank}, amount={final_amount}, missing_fields={missing_fields}")

    # If all required fields are present and we have llm_reply, set response
    # This handles cases where user provides optional fields (like narration) after all required fields are complete
    if (final_amount and final_recipient and final_bank and
        not missing_fields and llm_reply and
            not new_state.get("response")):
        new_state["response"] = llm_reply
        debug_log(
            "✅ extract_entities: All fields complete, setting response from llm_reply")

    if should_clear_stale_recipient and final_recipient:
        debug_log(
            f"❌ ERROR: Stale data clearing failed! recipient_account should be None but is {final_recipient}")
        new_state["recipient_account"] = None
        new_state["recipient_bank_code"] = None
        new_state["recipient_bank_name"] = None

    return cast(TransferState, new_state)
