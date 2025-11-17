"""Entity extraction node for transfer flow."""

from typing import Any, cast
import time

from apps.core.src.agent.models.transfer import SimpleTransferEntities
from apps.core.src.agent.models.transfer_extraction import TransferExtractionResult
from apps.core.src.agent.transfer.extractor import TransferEntityExtractor
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
    smart_context = {}
    if last_response:
        smart_context["previousResponse"] = last_response

    # Include beneficiaries in context to help extractor distinguish aliases from bank names
    beneficiaries = state.get("beneficiaries", [])
    if beneficiaries:
        smart_context["beneficiaries"] = beneficiaries

    result: TransferExtractionResult = await extractor.extract(state["message"], smart_context=smart_context if smart_context else None)

    entities = result.entities or SimpleTransferEntities()
    existing_amount = state.get("amount")

    debug_log(
        f"DEBUG extract_entities: Raw extraction - account='{entities.recipient_account}', bank_name='{entities.bank_name}', bank_code='{entities.bank_code}', amount='{entities.amount}'")

    existing_recipient_in_state = state.get("recipient_account")
    existing_bank_in_state = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")
    debug_log(
        f"DEBUG extract_entities: State before processing - existing recipient_account={existing_recipient_in_state}, bank={existing_bank_in_state}, existing_amount={existing_amount}")

    should_clear_stale_recipient = False
    current_flow_state = state.get("flow_state")
    transfer_status = state.get("transfer_status")

    # Do NOT clear recipient when only amount arrives.
    # Preserve previously extracted recipient/bank so we don't re-ask.
    if (entities.amount is not None and
        entities.recipient_account is None and
        entities.recipient_name is None and
            (existing_recipient_in_state or existing_bank_in_state)):
        debug_log(
            "ℹ️ extract_entities: Amount provided without new recipient; preserving existing recipient/bank.")
        should_clear_stale_recipient = False

    # Determine if this turn introduces a new recipient BEFORE applying updates
    prev_account = state.get("recipient_account")
    prev_bank_any = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")

    incoming_account_raw = entities.recipient_account
    incoming_account_norm = None
    if incoming_account_raw is not None:
        incoming_account_norm = str(incoming_account_raw).replace(
            " ", "").replace("-", "").replace("_", "").strip()

    incoming_bank_any = entities.bank_code or entities.bank_name

    # Detect actual changes only when the previous field existed.
    account_changed_pre_update = bool(
        incoming_account_norm and prev_account and incoming_account_norm != prev_account
    )
    bank_changed_pre_update = bool(
        incoming_bank_any and prev_bank_any and incoming_bank_any != prev_bank_any
    )
    recipient_changed_pre_update = account_changed_pre_update or bank_changed_pre_update
    had_prev_recipient = bool(prev_account or prev_bank_any)
    debug_log(
        f"DEBUG extract_entities: Pre-update compare - prev_account={prev_account}, prev_bank={prev_bank_any}, incoming_account_norm={incoming_account_norm}, incoming_bank_any={incoming_bank_any}, "
        f"account_changed_pre_update={account_changed_pre_update}, bank_changed_pre_update={bank_changed_pre_update}, recipient_changed_pre_update={recipient_changed_pre_update}")

    # New-transfer guard: if intent indicates a new transfer and a recipient arrives,
    # clear stale amount unless recipient is identical to previous
    classification_intent = (
        state.get("classification_result") or {}).get("intent", "")
    is_new_transfer_intent = classification_intent.lower(
    ) in {"transfer", "send_money", "send money", "send"}
    recipient_arrives_this_turn = bool(
        incoming_account_norm or incoming_bank_any or entities.recipient_name)

    should_clear_amount_pre_update = False
    if existing_amount is not None:
        # Only clear when an already-set field actually changes
        if (account_changed_pre_update or bank_changed_pre_update) and had_prev_recipient:
            should_clear_amount_pre_update = True
            debug_log(
                "ℹ️ extract_entities: Recipient changed (pre-update) with prior recipient. Will clear stale amount.")
        # Do NOT clear just because a new transfer intent arrives; only actual changes trigger clearing
    debug_log(
        f"DEBUG extract_entities: Pre-update amount clearing decision - existing_amount={existing_amount}, is_new_transfer_intent={is_new_transfer_intent}, recipient_arrives_this_turn={recipient_arrives_this_turn}, should_clear_amount_pre_update={should_clear_amount_pre_update}")

    new_state = dict(state)
    if should_clear_amount_pre_update:
        new_state["amount"] = None

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
        updates["_amount_set_at"] = time.time()
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
        updates["_recipient_established_at"] = time.time()

    if entities.bank_code is not None:
        updates["recipient_bank_code"] = entities.bank_code
        updates["_recipient_established_at"] = time.time()
    if entities.bank_name is not None:
        updates["recipient_bank_name"] = entities.bank_name
        debug_log(
            f"DEBUG extract_entities: Extracted bank_name: '{entities.bank_name}'")
        if entities.recipient_account is None:
            existing_account = new_state.get("recipient_account")
            if existing_account:
                debug_log(
                    f"ℹ️ extract_entities: Preserving existing account number '{existing_account}' when user provided bank name")
        updates["_recipient_established_at"] = time.time()

    if entities.source_account_id is not None:
        updates["source_account_id"] = entities.source_account_id
    if entities.narration is not None:
        updates["narration"] = entities.narration

    new_state.update(updates)

    # Retained safety: Clear stale amount if after updates the recipient differs from what amount was set against
    # (covers edge cases where earlier pre-update check did not trigger)
    post_incoming_account = incoming_account_norm
    post_incoming_bank = incoming_bank_any
    post_existing_account = new_state.get("recipient_account")
    post_existing_bank_any = new_state.get(
        "recipient_bank_code") or new_state.get("recipient_bank_name")
    is_new_account_post = bool(
        post_incoming_account and post_incoming_account != post_existing_account)
    is_new_bank_post = bool(
        post_incoming_bank and post_incoming_bank != post_existing_bank_any)
    if (is_new_account_post or is_new_bank_post) and new_state.get("amount") is not None:
        debug_log(
            "ℹ️ extract_entities: Post-update detected recipient mismatch -> clearing previous amount")
        new_state["amount"] = None
    debug_log(
        f"DEBUG extract_entities: Timestamps - _recipient_established_at={new_state.get('_recipient_established_at')}, _amount_set_at={new_state.get('_amount_set_at')}")

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
