"""Entity extraction node for transfer flow."""

import time
from typing import Any, cast

from apps.core.src.agent.graphs.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.graphs.transfer.models import TransferEntities
from apps.core.src.agent.graphs.transfer.models_extraction import TransferExtractionResult
from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.utils.logging import get_logger

from .utils import debug_log


async def extract_entities(
    state: TransferState,
    extractor: TransferEntityExtractor,
) -> TransferState:
    """Extract entities from user message."""

    transfer_status = state.get("transfer_status")
    flow_state = state.get("flow_state")
    message = state.get("message", "")

    logger = get_logger(__name__)

    logger.info(
        "extract_entities_entry",
        flow_state=flow_state,
        transfer_status=transfer_status,
        message=message[:50] if message else "",
        pin_verified=state.get("pin_verified"),
    )

    if transfer_status in ("authorized", "completed", "failed"):
        logger.info("extract_skipping_terminal", status=transfer_status)
        return state

    classification_result = state.get("classification_result")
    if classification_result:
        intent = classification_result.get("intent", "").lower()
        is_cancellation = intent == "cancel" or classification_result.get("is_cancellation") is True
        if is_cancellation:
            flow_state = state.get("flow_state")
            transfer_status = state.get("transfer_status")
            idem_key = state.get("idempotency_key")

            if flow_state not in ("extracting", "error", "cancelled", None) or transfer_status == "pending" or idem_key:
                debug_log(
                    f"🚫 Cancellation detected via classification result: flow_state={flow_state}, transfer_status={transfer_status}, idem_key={idem_key}"
                )
                return {
                    **state,
                    "flow_state": "cancelled",
                    "response": "",
                }

        if intent in ("manage_accounts", "query", "airtime", "data"):
            debug_log(f"🔀 Interrupt detected: {intent} - pausing transfer flow")
            return {
                **state,
                "flow_state": "paused",
                "interrupt_intent": intent,
                "response": "",
            }

    # CRITICAL: If resuming from an interrupt, skip extraction
    # User said "yes/ok" to resume, don't let LLM misclassify it as cancel
    if classification_result:
        complexity_reason = classification_result.get("complexity_reason", "")
        debug_log(f"🔎 [EXTRACTION] Checking skip logic. Reason: {complexity_reason}")
        if "Flow resume after interrupt" in complexity_reason:
            debug_log("⏭️ Skipping extraction - flow resume detected")
            return state
        else:
            debug_log("❌ [EXTRACTION] Not skipping - reason does not match 'Flow resume after interrupt'")
    else:
        debug_log("❌ [EXTRACTION] No classification result found in state")

    last_response = state.get("response") or state.get("llm_reply")
    smart_context = {}
    if last_response:
        smart_context["previousResponse"] = last_response

    beneficiaries = state.get("beneficiaries", [])
    if beneficiaries:
        smart_context["beneficiaries"] = beneficiaries

    language = state.get("language")
    if language:
        smart_context["language"] = language

    recent_transactions = state.get("recent_transactions", [])
    if recent_transactions:
        recent_transfers = [
            t for t in recent_transactions if t.get("type") == "transfer" and t.get("status") == "success"
        ][:3]
        if recent_transfers:
            smart_context["recentTransfers"] = recent_transfers

    message_to_extract = state.get("message", "")
    image_data = state.get("image_data")
    debug_log(f"🔍 [EXTRACTION] Extracting from message: '{message_to_extract}'")
    debug_log(
        f"🔍 [EXTRACTION] State before extraction - recipient_account={state.get('recipient_account')}, recipient_bank={state.get('recipient_bank_name') or state.get('recipient_bank_code')}, amount={state.get('amount')}"
    )

    result: TransferExtractionResult = await extractor.extract(
        message_to_extract,
        smart_context=smart_context if smart_context else None,
        image_data=image_data,
    )

    entities = result.entities or TransferEntities()
    existing_amount = state.get("amount")

    logger.info(
        "DEBUG_TRACE_AMOUNT_EXTRACTION_ENTRY",
        existing_amount=existing_amount,
        intent_new=(state.get("classification_result") or {}).get("intent"),
        extracted_amount=entities.amount,
        extracted_transfer_all=getattr(entities, "transfer_all", None),
    )

    debug_log(f"🔍 [EXTRACTION] State amount before extraction: {existing_amount}")

    debug_log(
        f"🔍 [EXTRACTION] Raw extraction - account='{entities.recipient_account}', bank_name='{entities.bank_name}', bank_code='{entities.bank_code}', amount='{entities.amount}'"
    )
    debug_log(f"🔍 [EXTRACTION] Missing fields: {result.missing_fields}")
    debug_log(f"🔍 [EXTRACTION] LLM reply: {result.reply}")

    existing_recipient_in_state = state.get("recipient_account")
    existing_bank_in_state = state.get("recipient_bank_code") or state.get("recipient_bank_name")
    debug_log(
        f"DEBUG extract_entities: State before processing - existing recipient_account={existing_recipient_in_state}, bank={existing_bank_in_state}, existing_amount={existing_amount}"
    )

    should_clear_stale_recipient = False
    transfer_status = state.get("transfer_status")

    # Do NOT clear recipient when only amount arrives.
    # Preserve previously extracted recipient/bank so we don't re-ask.
    if (
        entities.amount is not None
        and entities.recipient_account is None
        and entities.recipient_name is None
        and (existing_recipient_in_state or existing_bank_in_state)
    ):
        debug_log("ℹ️ extract_entities: Amount provided without new recipient; preserving existing recipient/bank.")
        should_clear_stale_recipient = False

    # Determine if this turn introduces a new recipient BEFORE applying updates
    prev_account = state.get("recipient_account")
    prev_bank_any = state.get("recipient_bank_code") or state.get("recipient_bank_name")

    incoming_account_raw = entities.recipient_account
    incoming_account_norm = None
    if incoming_account_raw is not None:
        incoming_account_norm = str(incoming_account_raw).replace(" ", "").replace("-", "").replace("_", "").strip()

    incoming_bank_any = entities.bank_code or entities.bank_name

    # Detect actual changes only when the previous field existed.
    account_changed_pre_update = bool(incoming_account_norm and prev_account and incoming_account_norm != prev_account)
    bank_changed_pre_update = bool(incoming_bank_any and prev_bank_any and incoming_bank_any != prev_bank_any)
    recipient_changed_pre_update = account_changed_pre_update or bank_changed_pre_update
    had_prev_recipient = bool(prev_account or prev_bank_any)
    debug_log(
        f"DEBUG extract_entities: Pre-update compare - prev_account={prev_account}, prev_bank={prev_bank_any}, incoming_account_norm={incoming_account_norm}, incoming_bank_any={incoming_bank_any}, "
        f"account_changed_pre_update={account_changed_pre_update}, bank_changed_pre_update={bank_changed_pre_update}, recipient_changed_pre_update={recipient_changed_pre_update}"
    )

    # New-transfer guard: if intent indicates a new transfer and a recipient arrives,
    # clear stale amount unless recipient is identical to previous
    classification_intent = (state.get("classification_result") or {}).get("intent", "")
    is_new_transfer_intent = classification_intent.lower() in {
        "transfer",
        "send_money",
        "send money",
        "send",
    }
    recipient_arrives_this_turn = bool(incoming_account_norm or incoming_bank_any or entities.recipient_name)

    should_clear_amount_pre_update = False
    if existing_amount is not None:
        # Only clear when an already-set field actually changes
        if (account_changed_pre_update or bank_changed_pre_update) and had_prev_recipient:
            should_clear_amount_pre_update = True
            debug_log(
                "ℹ️ extract_entities: Recipient changed (pre-update) with prior recipient. Will clear stale amount."
            )
        # Do NOT clear just because a new transfer intent arrives; only actual changes trigger clearing
    debug_log(
        f"DEBUG extract_entities: Pre-update amount clearing decision - existing_amount={existing_amount}, is_new_transfer_intent={is_new_transfer_intent}, recipient_arrives_this_turn={recipient_arrives_this_turn}, should_clear_amount_pre_update={should_clear_amount_pre_update}"
    )

    new_state = dict(state)
    if should_clear_amount_pre_update:
        new_state["amount"] = None

    if should_clear_stale_recipient:
        debug_log("🧹 CLEARING stale recipient data - setting all recipient fields to None")
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
            f"✓ AFTER CLEARING: recipient_account={new_state.get('recipient_account')}, recipient_bank={new_state.get('recipient_bank_code')}"
        )

    updates: dict[str, Any] = {
        "missing_fields": result.missing_fields or [],
        "llm_reply": result.reply,
        "flow_state": "extracting",
    }

    # Suppress LLM reply during account selection to avoid confusing messages
    # like "Sending ₦2" when user selects option 2
    if state.get("flow_state") == "selecting_account":
        updates["llm_reply"] = None
        debug_log("🔇 [EXTRACTION] Suppressing LLM reply during account selection")

    # Debug: Log what was extracted
    logger.info(
        "extraction_result",
        amount=entities.amount,
        transfer_all=getattr(entities, "transfer_all", None),
        transfer_percentage=getattr(entities, "transfer_percentage", None),
        recipient_name=entities.recipient_name,
        bank_name=entities.bank_name,
    )

    # CRITICAL: During account selection, ignore any amount extraction
    # User is just selecting "1" or "2" for account, not changing the amount
    flow_state = state.get("flow_state")
    if flow_state == "selecting_account" and entities.amount is not None:
        debug_log(f"🔍 [EXTRACTION] Ignoring amount extraction during account selection: {entities.amount}")
        entities.amount = None  # Prevent it from being treated as a new amount

    if entities.amount is not None:
        updates["amount"] = entities.amount
        updates["_amount_set_at"] = time.time()
        updates["transfer_all"] = False
        updates["transfer_percentage"] = None
        # CRITICAL: Reset transfer status to allow re-routing/re-checking funding
        updates["transfer_status"] = None
        updates["funding_required"] = False
        updates["funding_plan"] = None
        updates["idempotency_key"] = None  # CRITICAL: Force new key generation and Redis update
        updates["response"] = ""  # CRITICAL: Clear stale error messages from previous attempts
        updates["funding_error"] = None
    elif existing_amount is not None:
        # Preserve existing amount when user is providing other details (e.g., account details)
        # This is important for complex transfers where amount comes from task parameters
        # Use explicit None check to preserve amount even if it's 0 (though unlikely)
        updates["amount"] = existing_amount
        # Don't update _amount_set_at to preserve original timestamp
        logger.info("DEBUG_TRACE_AMOUNT_PRESERVED", existing_amount=existing_amount)
        debug_log(f"🔍 [EXTRACTION] Preserving existing amount: {existing_amount}")
    if entities.recipient_name is not None:
        updates["recipient_name"] = entities.recipient_name

    if entities.recipient_account is not None:
        normalized_account = str(entities.recipient_account).replace(" ", "").replace("-", "").replace("_", "").strip()
        updates["recipient_account"] = normalized_account
        debug_log(
            f"DEBUG extract_entities: Normalized account '{entities.recipient_account}' -> '{normalized_account}'"
        )
        if entities.bank_name is None and entities.bank_code is None:
            existing_bank_code = new_state.get("recipient_bank_code")
            existing_bank_name = new_state.get("recipient_bank_name")
            if existing_bank_code or existing_bank_name:
                debug_log(
                    f"ℹ️ extract_entities: Preserving existing bank name '{existing_bank_name}' when user provided account number"
                )
        updates["_recipient_established_at"] = time.time()

    if entities.bank_code is not None:
        updates["recipient_bank_code"] = entities.bank_code
        updates["_recipient_established_at"] = time.time()
        debug_log(f"🔍 [EXTRACTION] Adding bank_code to updates: '{entities.bank_code}'")
    if entities.bank_name is not None:
        updates["recipient_bank_name"] = entities.bank_name
        debug_log(f"🔍 [EXTRACTION] Adding bank_name to updates: '{entities.bank_name}'")
        if entities.recipient_account is None:
            existing_account = new_state.get("recipient_account")
            if existing_account:
                debug_log(
                    f"ℹ️ extract_entities: Preserving existing account number '{existing_account}' when user provided bank name"
                )
        updates["_recipient_established_at"] = time.time()


    if entities.source_bank_name is not None:
        updates["source_bank_name"] = entities.source_bank_name
        debug_log(f"🔍 [EXTRACTION] Adding source_bank_name to updates: '{entities.source_bank_name}'")
    if entities.narration is not None:
        updates["narration"] = entities.narration

    # Detect internal transfer (user wants to move between their own accounts)
    # Pattern: source_bank_name + bank_name (destination) WITHOUT recipient_account AND WITHOUT recipient_name
    # If recipient_name is present (e.g., "mum's gtb"), it's an EXTERNAL transfer, not internal
    is_internal_transfer = (
        entities.source_bank_name is not None
        and entities.bank_name is not None
        and entities.recipient_account is None
        and entities.recipient_name is None  # No recipient = it's user's own account
    )
    if is_internal_transfer:
        updates["is_internal_transfer"] = True
        debug_log(f"🔄 [EXTRACTION] Internal transfer detected: {entities.source_bank_name} -> {entities.bank_name}")

    if getattr(entities, "transfer_all", None) is True:
        updates["transfer_all"] = True
        # Only set amount=None if user didn't provide explicit amount (e.g., "send all", not "send 40k from all accounts")
        if entities.amount is None:
            updates["amount"] = None  # Will be resolved in check_funding node
            debug_log("💰 [EXTRACTION] Transfer all detected - amount will be set from balance in check_funding")
        else:
            debug_log(f"💰 [EXTRACTION] Transfer all with explicit amount: ₦{entities.amount:,.0f}")
    elif state.get("transfer_all") and entities.amount is None:
        # Preserve existing transfer_all flag if not in new extraction
        updates["transfer_all"] = state["transfer_all"]
        debug_log("🔍 [EXTRACTION] Preserving existing transfer_all flag")

    if getattr(entities, "transfer_percentage", None):
        updates["transfer_percentage"] = entities.transfer_percentage
        # Set amount=None if user didn't provide explicit amount
        if entities.amount is None:
            updates["amount"] = None  # Will be calculated from percentage in check_funding
            debug_log(
                f"💰 [EXTRACTION] Transfer percentage detected: {entities.transfer_percentage}% - amount will be calculated in check_funding"
            )
        else:
            debug_log(
                f"💰 [EXTRACTION] Transfer percentage with explicit amount: {entities.transfer_percentage}%, ₦{entities.amount:,.0f}"
            )
    elif state.get("transfer_percentage") and entities.amount is None:
        # Preserve existing transfer_percentage if not in new extraction
        updates["transfer_percentage"] = state["transfer_percentage"]
        debug_log(f"🔍 [EXTRACTION] Preserving existing transfer_percentage: {state['transfer_percentage']}%")

    if getattr(entities, "source_accounts", None):
        updates["source_accounts"] = entities.source_accounts[:2]
        debug_log(f"💳 [EXTRACTION] Dual-account pooling: {updates['source_accounts']}")
    if getattr(entities, "use_dual_accounts", None) is True:
        updates["use_dual_accounts"] = True
        debug_log("💳 [EXTRACTION] User wants to use both accounts")
    if getattr(entities, "explicit_split", None):
        updates["explicit_split"] = entities.explicit_split
        debug_log(f"💳 [EXTRACTION] Explicit split: {updates['explicit_split']}")

    debug_log(f"🔍 [EXTRACTION] Updates to apply: {updates}")

    new_state.update(updates)

    debug_log(
        f"🔍 [EXTRACTION] State after updates - recipient_account={new_state.get('recipient_account')}, recipient_bank={new_state.get('recipient_bank_name') or new_state.get('recipient_bank_code')}"
    )

    # Retained safety: Clear stale amount if after updates the recipient differs from what amount was set against
    # BUT: Don't clear if user is providing account details for an existing task (same recipient_name)
    post_incoming_account = incoming_account_norm
    post_incoming_bank = incoming_bank_any
    post_existing_account = new_state.get("recipient_account")
    post_existing_bank_any = new_state.get("recipient_bank_code") or new_state.get("recipient_bank_name")
    post_existing_recipient_name = new_state.get("recipient_name")
    incoming_recipient_name = entities.recipient_name

    is_same_recipient = (
        post_existing_recipient_name
        and incoming_recipient_name
        and str(post_existing_recipient_name).lower() == str(incoming_recipient_name).lower()
    ) or (post_existing_recipient_name and not incoming_recipient_name and post_existing_recipient_name)

    is_new_account_post = bool(post_incoming_account and post_incoming_account != post_existing_account)
    is_new_bank_post = bool(post_incoming_bank and post_incoming_bank != post_existing_bank_any)

    # Only clear amount if recipient actually changed (different account/bank AND different recipient name)
    # Don't clear if user is just providing account details for the same recipient
    # AND Don't clear if user is filling in a missing recipient for an existing amount (e.g. "Send 50k" -> "to John")
    was_empty_recipient = not (prev_account or prev_bank_any)

    if (is_new_account_post or is_new_bank_post) and new_state.get("amount") is not None:
        if not is_same_recipient:
            if was_empty_recipient and not is_new_transfer_intent:
                debug_log("ℹ️ extract_entities: Filling in missing recipient for existing amount. Preserving amount.")
            else:
                debug_log("ℹ️ extract_entities: Post-update detected recipient mismatch -> clearing previous amount")
                new_state["amount"] = None
        else:
            debug_log("ℹ️ extract_entities: Same recipient, preserving amount when providing account details")
    debug_log(
        f"DEBUG extract_entities: Timestamps - _recipient_established_at={new_state.get('_recipient_established_at')}, _amount_set_at={new_state.get('_amount_set_at')}"
    )

    final_recipient = new_state.get("recipient_account")
    final_bank = new_state.get("recipient_bank_code") or new_state.get("recipient_bank_name")
    final_amount = new_state.get("amount")
    missing_fields = new_state.get("missing_fields", [])
    llm_reply = new_state.get("llm_reply")

    debug_log(
        f"✓ extract_entities FINAL STATE: recipient_account={final_recipient}, recipient_bank={final_bank}, amount={final_amount}, missing_fields={missing_fields}"
    )

    # If all required fields are present and we have llm_reply, set response
    # This handles cases where user provides optional fields (like narration) after all required fields are complete
    if (
        final_amount
        and final_recipient
        and final_bank
        and not missing_fields
        and llm_reply
        and not new_state.get("response")
    ):
        new_state["response"] = llm_reply
        debug_log("✓ extract_entities: All fields complete, setting response from llm_reply")

    if should_clear_stale_recipient and final_recipient:
        debug_log(f"❌ ERROR: Stale data clearing failed! recipient_account should be None but is {final_recipient}")
        new_state["recipient_account"] = None
        new_state["recipient_bank_code"] = None
        new_state["recipient_bank_name"] = None

    return cast(TransferState, new_state)
