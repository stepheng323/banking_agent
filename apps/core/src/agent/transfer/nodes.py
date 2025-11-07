"""LangGraph nodes for transfer flow."""

from typing import Any, cast
import hashlib
import json
import redis.asyncio as redis

from apps.core.src.agent.services.account_selection_service import AccountSelectionService
from apps.core.src.agent.transfer.state import TransferState
from apps.core.src.agent.models.transfer_extraction import TransferExtractionResult
from apps.core.src.agent.models.transfer import SimpleTransferEntities
from apps.core.src.agent.services.transfer_entity_extractor import TransferEntityExtractor
from apps.core.src.agent.services.beneficiary_matcher import BeneficiaryMatcher
from apps.core.src.agent.services.validation_service import AsyncValidationService
from apps.core.src.agent.formatters.transfer import format_transfer_summary
from apps.core.src.agent.common.cancellation import (
    is_cancellation_intent,
    handle_transaction_cancellation,
)

from shared.cache.bank_cache import BankCacheService
from shared.utils.serialization import sqlalchemy_to_dict
from shared.database import Account
from shared.database.models import Beneficiary
from shared.config import settings
from shared.clients.whatsapp_client import WhatsAppClient


async def extract_entities(
    state: TransferState,
    extractor: TransferEntityExtractor,
) -> TransferState:
    """Extract entities from user message."""
    # Check for cancellation intent first (using LLM classification if available)
    message = state.get("message", "")
    phone_number = state.get("phone_number")

    # Use LLM-based cancellation detection (checks classification result from orchestrator)
    if await is_cancellation_intent(message, phone_number=phone_number):
        flow_state = state.get("flow_state")
        transfer_status = state.get("transfer_status")
        idem_key = state.get("idempotency_key")

        # Only handle cancellation if there's an active transaction
        if (flow_state not in ("extracting", "error", "cancelled", None) or
            transfer_status == "pending" or
                idem_key):
            # Mark for cancellation handling - will be routed to cancel node
            print(
                f"🚫 Cancellation detected in extract_entities: flow_state={flow_state}, transfer_status={transfer_status}, idem_key={idem_key}")
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

    print(
        f"DEBUG extract_entities: Raw extraction - account='{entities.recipient_account}', bank_name='{entities.bank_name}', bank_code='{entities.bank_code}', amount='{entities.amount}'")

    new_state = dict(state)
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
        print(
            f"DEBUG extract_entities: Normalized account '{entities.recipient_account}' -> '{normalized_account}'")
    if entities.bank_code is not None:
        updates["recipient_bank_code"] = entities.bank_code
    if entities.bank_name is not None:
        updates["recipient_bank_name"] = entities.bank_name
        print(
            f"DEBUG extract_entities: Extracted bank_name: '{entities.bank_name}'")
    if entities.source_account_id is not None:
        updates["source_account_id"] = entities.source_account_id
    if entities.narration is not None:
        updates["narration"] = entities.narration

    new_state.update(updates)
    return cast(TransferState, new_state)


async def load_user_context(
    state: TransferState,
    user_cache: Any,
    account_repo: Any,
    beneficiary_repo: Any,
) -> TransferState:
    """Load user context (profile, accounts, beneficiaries)."""
    phone = state["phone_number"]
    ctx = await user_cache.get(phone) or {}
    profile = ctx.get("profile") or {}
    accounts = ctx.get("accounts") or []
    beneficiaries_list = ctx.get("beneficiaries") or []

    user_id = profile.get("id") if isinstance(profile, dict) else None
    if user_id:
        try:
            if not beneficiaries_list:
                beneficiaries_list = beneficiary_repo.get_by_user(str(user_id))
            if not accounts:
                db_accounts = account_repo.get_by_user(str(user_id))
                accounts = db_accounts or []
        except Exception:
            pass

    accounts_dict = [
        sqlalchemy_to_dict(acc) if isinstance(acc, Account) else acc
        for acc in accounts
    ]
    beneficiaries_dict = [
        sqlalchemy_to_dict(b) if isinstance(b, Beneficiary) else b
        for b in beneficiaries_list
    ]

    new_state = dict(state)
    new_state.update({
        "user_profile": profile,
        "accounts": accounts_dict,
        "beneficiaries": beneficiaries_dict,
    })

    return cast(TransferState, new_state)


async def validate_amount(state: TransferState) -> TransferState:
    """Validate that amount is present."""
    if not state.get("amount"):
        return {
            **state,
            "flow_state": "collecting_amount",
            "response": state.get("llm_reply") or "How much should I send?",
        }
    return state


async def select_source_account(
    state: TransferState,
) -> TransferState:
    """Select source account using standalone account selection service."""

    accounts = state.get("accounts", [])
    profile = state.get("user_profile", {})
    source_account_id = state.get("source_account_id")
    llm_reply = state.get("llm_reply")

    print(
        f"DEBUG select_source_account: accounts={len(accounts)}, source_account_id={source_account_id}")

    selected, response = AccountSelectionService.select_account(
        accounts=accounts,
        profile=profile or {},
        source_account_id=source_account_id,
        llm_reply=llm_reply,
    )

    print(
        f"DEBUG select_source_account: selected={selected is not None}, response={response is not None}")

    if selected is not None:
        # Account auto-selected - clear response and continue flow
        print(
            f"DEBUG select_source_account: Auto-selected account: {selected.get('id')}")
        return {
            **state,
            "selected_source_account": selected,
            "response": "",  # Clear any previous response
        }

    return {
        **state,
        "flow_state": "selecting_account",
        "response": response or "Please select an account.",
    }


async def find_beneficiary(
    state: TransferState,
    matcher: BeneficiaryMatcher,
) -> TransferState:
    """Find beneficiary by name (optional convenience feature). Account resolution happens via banking API."""
    rec_name = state.get("recipient_name")
    acct_number = state.get("recipient_account")
    bank_code = state.get("recipient_bank_code")
    bank_name = state.get("recipient_bank_name")
    beneficiaries = state.get("beneficiaries", [])

    if rec_name and not (acct_number and (bank_code or bank_name)):
        beneficiaries_models = [
            Beneficiary(**b) if isinstance(b, dict) else b
            for b in beneficiaries
        ]
        status, single, candidates = matcher.match(
            rec_name, beneficiaries_models)

        if status == "single" and single:
            return {
                **state,
                "recipient_account": str(single.account_number),
                "recipient_bank_code": str(single.bank_code),
                "recipient_bank_name": str(single.bank_name),
                "recipient_name": str(single.account_name or single.alias or rec_name),
                "matched_beneficiary": sqlalchemy_to_dict(single) if hasattr(single, "__table__") else single,
            }
        elif status == "clarify" and candidates:
            opts = "; ".join([
                f"{(b.account_name or b.alias)} ({b.bank_name} • {str(b.account_number)[-4:]})"
                for b in candidates
            ])
            return {
                **state,
                "matched_beneficiary": None,  # Clear stale beneficiary - need clarification
                "flow_state": "collecting_recipient",
                "response": state.get("llm_reply") or f"I found multiple matches for '{rec_name}'. Which one? {opts}",
            }
        else:
            # No match found - clear any stale matched_beneficiary
            return {
                **state,
                "matched_beneficiary": None,  # Clear stale beneficiary - no match found
                "flow_state": "collecting_recipient",
                "response": state.get("llm_reply") or "Please provide the account number and bank name.",
            }

    if acct_number and not (bank_code or bank_name):
        # User provided account but not bank - clear any stale matched_beneficiary
        # (since we're collecting bank info, the beneficiary match is no longer valid)
        return {
            **state,
            "matched_beneficiary": None,  # Clear stale beneficiary - bank info missing
            "flow_state": "collecting_recipient",
            "response": state.get("llm_reply") or "Please provide the bank name for this account.",
        }

    if not acct_number or not (bank_code or bank_name):
        # Missing account or bank - clear any stale matched_beneficiary
        return {
            **state,
            "matched_beneficiary": None,  # Clear stale beneficiary - missing info
            "flow_state": "collecting_recipient",
            "response": state.get("llm_reply") or "Please provide the account number and bank name.",
        }

    # If we have account and bank but no matched_beneficiary, that's fine
    # (user provided account details directly, not through beneficiary matching)
    # Clear any stale matched_beneficiary if it doesn't match current recipient
    matched_beneficiary = state.get("matched_beneficiary")
    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_account = str(
            matched_beneficiary.get("account_number", ""))
        beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))
        if (beneficiary_account != str(acct_number) or
                beneficiary_bank_code != str(bank_code)):
            # Stale matched_beneficiary - clear it
            return {
                **state,
                "matched_beneficiary": None,
            }

    return state


async def validate_parallel(
    state: TransferState,
    validation_service: AsyncValidationService,
    bank_cache: BankCacheService,
    fetch_banks_func: Any,
) -> TransferState:
    """Parallel validation: resolve account + check balance."""
    if not validation_service:
        return state

    source = state.get("selected_source_account")
    if not source or not source.get("id"):
        return state

    acct_number = state.get("recipient_account")
    bank_code = state.get("recipient_bank_code")
    bank_name = state.get("recipient_bank_name")

    print(
        f"DEBUG validate_parallel: Initial state - account='{acct_number}', bank_code='{bank_code}', bank_name='{bank_name}'")

    if bank_code and bank_name and bank_cache:
        cache_ready = await bank_cache.ensure_banks_cached(fetch_banks_func)
        if cache_ready:
            correct_code = await bank_cache.get_bank_code(bank_name)
            if correct_code and correct_code != bank_code:
                print(
                    f"DEBUG validate_parallel: Bank code mismatch! bank_name='{bank_name}' should be '{correct_code}' but got '{bank_code}', correcting...")
                bank_code = correct_code
                state = {
                    **state,
                    "recipient_bank_code": correct_code,
                }
            elif not correct_code:
                print(
                    f"DEBUG validate_parallel: Could not resolve bank_code for bank_name='{bank_name}', but bank_code='{bank_code}' exists. Proceeding with existing code.")

    # Normalize account number (remove spaces, dashes, etc.) and update state
    if acct_number:
        normalized_account = str(acct_number).replace(
            " ", "").replace("-", "").replace("_", "").strip()
        print(
            f"DEBUG validate_parallel: Normalizing account '{acct_number}' -> '{normalized_account}'")
        if normalized_account != str(acct_number):
            state = {
                **state,
                "recipient_account": normalized_account,
            }
        acct_number = normalized_account

    if not bank_code and bank_name and bank_cache:
        print(
            f"DEBUG validate_parallel: Resolving bank_code from bank_name: '{bank_name}'")
        cache_ready = await bank_cache.ensure_banks_cached(fetch_banks_func)
        if cache_ready:
            resolved_code = await bank_cache.get_bank_code(bank_name)
            print(
                f"DEBUG validate_parallel: Resolved bank_code: '{resolved_code}' for bank_name: '{bank_name}'")
            if resolved_code:
                bank_code = resolved_code
                state = {
                    **state,
                    "recipient_bank_code": resolved_code,
                }
            else:
                print(
                    f"DEBUG validate_parallel: Failed to resolve bank_code for '{bank_name}'")
                return {
                    **state,
                    "flow_state": "error",
                    "response": state.get("llm_reply") or f"I couldn't find a bank code for '{bank_name}'. Please provide the bank name or code.",
                    "validation_errors": ["bank_code_resolution_failed"],
                }
        else:
            print("""DEBUG validate_parallel: Bank cache not ready""")
            return {
                **state,
                "flow_state": "validating",
            }

    if not bank_code:
        print("""DEBUG validate_parallel: No bank_code available""")
        return {
            **state,
            "flow_state": "error",
            "response": state.get("llm_reply") or "Bank code is required for validation. Please provide the bank name or code.",
            "validation_errors": ["missing_bank_code"],
        }

    if not acct_number:
        print("""DEBUG validate_parallel: No account_number available""")
        return {
            **state,
            "flow_state": "error",
            "response": state.get("llm_reply") or "Account number is required for validation.",
            "validation_errors": ["missing_account_number"],
        }

    # Check if recipient is a matched beneficiary - use DB data instead of API
    # Edge cases handled:
    # 1. Beneficiary matched and matches current recipient → reuse/reconstruct from beneficiary
    # 2. Beneficiary exists but doesn't match current recipient (stale) → clear and validate via API
    # 3. No beneficiary → validate via API
    # 4. Recipient changed from beneficiary to non-beneficiary → clear and validate via API
    matched_beneficiary = state.get("matched_beneficiary")
    account_resolved = state.get("account_resolved")

    # Get current recipient details (source of truth)
    current_account = state.get("recipient_account")
    current_bank_code = state.get("recipient_bank_code")

    # Only use beneficiary data if matched_beneficiary exists AND matches current recipient
    use_beneficiary = False
    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_account = str(
            matched_beneficiary.get("account_number", ""))
        beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))

        # Verify the matched beneficiary actually matches current recipient details
        # This handles stale matched_beneficiary from previous transfers
        if (beneficiary_account and beneficiary_bank_code and
            current_account and current_bank_code and
            str(current_account) == beneficiary_account and
                str(current_bank_code) == beneficiary_bank_code):
            use_beneficiary = True
            print(
                f"DEBUG validate_parallel: Valid beneficiary match found (account={current_account}, bank={current_bank_code})")
        else:
            # Matched beneficiary doesn't match current recipient - it's stale!
            print(f"DEBUG validate_parallel: Stale matched_beneficiary detected. "
                  f"Beneficiary: account={beneficiary_account}, bank={beneficiary_bank_code}. "
                  f"Current: account={current_account}, bank={current_bank_code}. "
                  f"Clearing and validating via API.")
            # Clear the stale matched_beneficiary
            state = {
                **state,
                "matched_beneficiary": None,
            }
            matched_beneficiary = None

    if use_beneficiary and matched_beneficiary:
        # ✅ Valid beneficiary match - use beneficiary data
        # Check if we can reuse existing account_resolved
        if account_resolved and isinstance(account_resolved, dict):
            # Verify existing resolution matches current recipient
            if (account_resolved.get("account_number") == str(current_account) and
                    account_resolved.get("bank_code") == str(current_bank_code)):
                print(
                    f"DEBUG validate_parallel: Reusing existing account_resolved for beneficiary")
                resolved = account_resolved
                balance = None
            else:
                # Existing resolution doesn't match - reconstruct from beneficiary
                print(
                    f"DEBUG validate_parallel: Reconstructing account_resolved for beneficiary (existing resolution doesn't match)")
                resolved = {
                    "success": True,
                    "account_name": state.get("recipient_name") or matched_beneficiary.get("account_name") or matched_beneficiary.get("alias", ""),
                    "account_number": current_account,
                    "bank_code": current_bank_code,
                    "provider": "database",
                }
                balance = None
        else:
            # No existing resolution - construct from beneficiary
            print(f"DEBUG validate_parallel: Using beneficiary data from database")
            resolved = {
                "success": True,
                "account_name": state.get("recipient_name") or matched_beneficiary.get("account_name") or matched_beneficiary.get("alias", ""),
                "account_number": current_account,
                "bank_code": current_bank_code,
                "provider": "database",
            }
            balance = None
    else:
        # ❌ No valid beneficiary match - validate via API
        # This handles:
        # - No matched_beneficiary
        # - Stale matched_beneficiary (already cleared above)
        # - Recipient changed to non-beneficiary
        if not current_account or not current_bank_code:
            return {
                **state,
                "flow_state": "error",
                "response": state.get("llm_reply") or "Account number and bank code are required for validation.",
                "validation_errors": ["missing_recipient_details"],
            }

        print(
            f"DEBUG validate_parallel: Calling validation API with account_number='{acct_number}', bank_code='{bank_code}'")
        resolved, balance = await validation_service.validate_account_and_balance(
            account_number=str(acct_number),
            bank_code=str(bank_code),
            source_account_id=str(source.get("id")),
        )
        print(
            f"DEBUG validate_parallel: Validation result - resolved={resolved is not None}, balance={balance is not None}")
        if resolved:
            print(
                f"DEBUG validate_parallel: Account resolved successfully: {resolved}")

        # Check if resolution failed (None or success=False)
        if resolved is None or (isinstance(resolved, dict) and not resolved.get("success", False)):
            return {
                **state,
                "flow_state": "error",
                "response": state.get("llm_reply") or "I couldn't verify that account right now. Please confirm the account number and bank.",
                "validation_errors": ["account_resolution_failed"],
            }

    # Account resolution succeeded - proceed even if balance check failed
    # Balance check is optional and not all providers support it
    available = None
    if balance:
        try:
            available = float(balance.get("available", 0)) if balance else None
            amount_value = state.get("amount")
            if amount_value is not None and available is not None:
                amount = float(amount_value)
                if available < amount:
                    return {
                        **state,
                        "flow_state": "error",
                        "response": state.get("llm_reply") or "Insufficient balance in the selected account. Choose another account.",
                        "validation_errors": ["insufficient_balance"],
                    }
        except Exception as e:
            print(f"⚠️  Balance check failed (non-critical): {e}")
            available = None

    # Proceed with transfer - account is valid
    # Clear any previous validation errors since account resolution succeeded
    # Store initial values in Redis for change detection
    idem_key = state.get("idempotency_key")
    phone_number = state.get("phone_number")

    # Get or create idempotency key if not exists
    if not idem_key:
        idem_key = hashlib.sha256(
            f"{phone_number}|{state.get('amount')}|{acct_number}|{bank_name}".encode(
                "utf-8")
        ).hexdigest()

    # Store initial values in Redis for change detection
    if phone_number and idem_key:
        prev_key = f"transfer:prev:{phone_number}:{idem_key}"
        prev_values = {
            "amount": state.get("amount"),
            "recipient_account": acct_number,
            "recipient_bank_code": bank_code,
            "recipient_bank_name": bank_name,
            "recipient_name": resolved.get("account_name") if isinstance(resolved, dict) else state.get("recipient_name"),
        }
        await bank_cache.redis.set(
            prev_key,
            json.dumps(prev_values),
            ex=3600  # 1 hour expiry
        )

    return {
        **state,
        "account_resolved": resolved,
        "balance_available": available,
        "flow_state": "validating",
        "validation_errors": [],  # Clear any previous validation errors
        "idempotency_key": idem_key,
    }


async def check_and_acknowledge_changes(
    state: TransferState,
    bank_cache: BankCacheService,
) -> TransferState:
    """
    Check for changes in transfer details and show acknowledgment message.
    Returns state with acknowledgment message if changes detected.
    """
    # Only check if we have a transfer in progress (account already resolved)
    account_resolved = state.get("account_resolved")
    selected_account = state.get("selected_source_account")

    if not account_resolved or not selected_account:
        return state

    # If we already acknowledged changes, skip
    if state.get("_change_acknowledged"):
        return state

    # Get current values
    current_amount = state.get("amount")
    current_account = state.get("recipient_account")
    current_bank_code = state.get("recipient_bank_code")
    current_bank_name = state.get("recipient_bank_name")
    current_recipient_name = state.get("recipient_name")

    # Get previous values from Redis
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")

    if not phone_number or not idem_key:
        # No previous values to compare, store current as previous
        if phone_number and idem_key:
            prev_key = f"transfer:prev:{phone_number}:{idem_key}"
            prev_values = {
                "amount": current_amount,
                "recipient_account": current_account,
                "recipient_bank_code": current_bank_code,
                "recipient_bank_name": current_bank_name,
                "recipient_name": current_recipient_name or (
                    account_resolved.get("account_name") if isinstance(
                        account_resolved, dict) else None
                ),
            }
            await bank_cache.redis.set(
                prev_key,
                json.dumps(prev_values),
                ex=3600
            )
        return state

    prev_key = f"transfer:prev:{phone_number}:{idem_key}"
    try:
        prev_data = await bank_cache.redis.get(prev_key)
        if not prev_data:
            # No previous data, store current as previous
            prev_values = {
                "amount": current_amount,
                "recipient_account": current_account,
                "recipient_bank_code": current_bank_code,
                "recipient_bank_name": current_bank_name,
                "recipient_name": current_recipient_name or (
                    account_resolved.get("account_name") if isinstance(
                        account_resolved, dict) else None
                ),
            }
            await bank_cache.redis.set(
                prev_key,
                json.dumps(prev_values),
                ex=3600
            )
            return state

        prev_values = json.loads(prev_data)
        previous_amount = prev_values.get("amount")
        previous_account = prev_values.get("recipient_account")
        previous_bank_code = prev_values.get("recipient_bank_code")
        previous_bank_name = prev_values.get("recipient_bank_name")
        previous_recipient_name = prev_values.get("recipient_name")
    except Exception as e:
        print(f"⚠️  Error loading previous values from Redis: {e}")
        return state

    # Detect changes
    changes = []
    recipient_info_changed = False

    if current_amount and previous_amount and current_amount != previous_amount:
        amount_str = f"₦{current_amount:,.0f}"
        if current_amount == int(current_amount):
            amount_str = amount_str.replace('.0', '')
        changes.append(f"amount to {amount_str}")

    if current_account and previous_account and str(current_account) != str(previous_account):
        changes.append(f"account number to {current_account}")
        recipient_info_changed = True

    if current_bank_code and previous_bank_code and str(current_bank_code) != str(previous_bank_code):
        bank_name = current_bank_name or current_bank_code
        changes.append(f"bank to {bank_name}")
        recipient_info_changed = True
    elif current_bank_name and previous_bank_name and current_bank_name != previous_bank_name:
        changes.append(f"bank to {current_bank_name}")
        recipient_info_changed = True

    if current_recipient_name and previous_recipient_name and current_recipient_name != previous_recipient_name:
        changes.append(f"recipient to {current_recipient_name.title()}")
        recipient_info_changed = True

    # If recipient info changed, clear account_resolved to trigger re-validation
    new_state_updates = {}
    if recipient_info_changed:
        print(f"DEBUG check_and_acknowledge_changes: Recipient info changed, clearing account_resolved for re-validation")
        new_state_updates["account_resolved"] = None

        # Clear matched_beneficiary only if it doesn't match the new recipient
        # (find_beneficiary may have already matched a new beneficiary, so we should preserve it)
        matched_beneficiary = state.get("matched_beneficiary")
        if matched_beneficiary and isinstance(matched_beneficiary, dict):
            beneficiary_account = str(
                matched_beneficiary.get("account_number", ""))
            beneficiary_bank_code = str(
                matched_beneficiary.get("bank_code", ""))
            # Only clear if it doesn't match the new recipient
            if (beneficiary_account != str(current_account) or
                    beneficiary_bank_code != str(current_bank_code)):
                print(f"DEBUG check_and_acknowledge_changes: Clearing stale matched_beneficiary "
                      f"(beneficiary: {beneficiary_account}/{beneficiary_bank_code} != "
                      f"current: {current_account}/{current_bank_code})")
                new_state_updates["matched_beneficiary"] = None
            else:
                print(f"DEBUG check_and_acknowledge_changes: Preserving matched_beneficiary "
                      f"(still matches new recipient: {current_account}/{current_bank_code})")

        # Reset so it can check again after re-validation
        new_state_updates["_change_acknowledged"] = False

    # If changes detected, show acknowledgment
    if changes:
        if len(changes) == 1:
            message = f"Ok, changing {changes[0]}."
        elif len(changes) == 2:
            message = f"Ok, changing {changes[0]} and {changes[1]}."
        else:
            message = f"Ok, changing {', '.join(changes[:-1])}, and {changes[-1]}."

        result = {
            **state,
            **new_state_updates,
            "response": message,
            "_change_acknowledged": True,
            "flow_state": "validating",  # Will route appropriately
        }
        return cast(TransferState, result)

    # No changes detected, update previous values in Redis
    prev_values = {
        "amount": current_amount,
        "recipient_account": current_account,
        "recipient_bank_code": current_bank_code,
        "recipient_bank_name": current_bank_name,
        "recipient_name": current_recipient_name or (
            account_resolved.get("account_name") if isinstance(
                account_resolved, dict) else None
        ),
    }
    await bank_cache.redis.set(
        prev_key,
        json.dumps(prev_values),
        ex=3600
    )

    return state


async def prepare_confirmation(
    state: TransferState,
    whatsapp_client: WhatsAppClient,
    redis_client: redis.Redis,
) -> TransferState:
    """Prepare transfer confirmation summary."""
    print(f"DEBUG prepare_confirmation: state={json.dumps(state, indent=2)}")
    amount = state.get("amount")
    account_resolved = state.get("account_resolved")
    rec_name = (
        account_resolved.get("account_name") if account_resolved and isinstance(account_resolved, dict)
        else state.get("recipient_name") or "Recipient"
    )
    bank_name = state.get("recipient_bank_name") or state.get(
        "recipient_bank_code") or ""
    acct_number = state.get("recipient_account")
    source = state.get("selected_source_account", {})
    narration = state.get("narration")

    summary = format_transfer_summary({
        "amount": float(amount or 0),
        "recipientName": rec_name,
        "recipientBank": bank_name,
        "recipientAccount": str(acct_number),
        "sourceBank": str(source.get("name") or source.get("bank_name") or "Account"),
        "sourceAccount": str(source.get("account_number") or source.get("number") or source.get("id") or ""),
        "narration": narration,
    })

    idem_key = hashlib.sha256(
        f"{state['phone_number']}|{amount}|{acct_number}|{bank_name}".encode(
            "utf-8")
    ).hexdigest()

    pending = {
        "phone": state["phone_number"],
        "amount": amount,
        "recipient": {
            "name": rec_name,
            "account_number": acct_number,
            "bank_code": state.get("recipient_bank_code"),
            "bank_name": bank_name,
        },
        "source": {
            "id": source.get("id"),
            "name": source.get("name"),
            "type": source.get("type"),
        },
        "idempotency_key": idem_key,
        "status": "awaiting_confirmation",
    }

    await redis_client.set(
        f"user:{state['phone_number']}:pending_transfer",
        json.dumps(pending),
        ex=900
    )

    token = f"transfer-pin-{idem_key}"
    await redis_client.set(
        f"user:{state['phone_number']}:pending_transfer_flow_token",
        token,
        ex=900
    )

    # Store phone number mapping for flow_token lookup
    await redis_client.set(
        f"transfer:token:{idem_key}:phone",
        state["phone_number"],
        ex=900
    )

    # Store initial previous values if not already stored
    prev_key = f"transfer:prev:{state['phone_number']}:{idem_key}"
    prev_data = await redis_client.get(prev_key)
    if not prev_data:
        prev_values = {
            "amount": amount,
            "recipient_account": acct_number,
            "recipient_bank_code": state.get("recipient_bank_code"),
            "recipient_bank_name": bank_name,
            "recipient_name": rec_name,
        }
        await redis_client.set(
            prev_key,
            json.dumps(prev_values),
            ex=3600
        )

    await whatsapp_client.send_flow(
        to=state["phone_number"],
        header="Confirm Your Transfer",
        flow_cta="Authorize Transfer",
        flow_id=settings.pin_confirmation_flow_id,
        screen_name="Pin",
        flow_token=token,
        text_body=summary,
    )

    return {
        **state,
        "response": "",
        "idempotency_key": idem_key,
        "transfer_status": "pending",
        "flow_state": "confirming",
    }


async def handle_cancellation(
    state: TransferState,
    redis_client: redis.Redis,
) -> TransferState:
    """
    Handle cancellation of transfer in progress.
    Uses shared cancellation utilities for consistency across transaction types.
    """
    print(
        f"🛑 handle_cancellation called: flow_state={state.get('flow_state')}, amount={state.get('amount')}, recipient={state.get('recipient_name')}")
    # Convert TransferState to dict for the generic handler
    state_dict = dict(state)
    result = await handle_transaction_cancellation(
        state=state_dict,
        transaction_type="transfer",
        redis_client=redis_client,
    )
    print(
        f"✅ handle_cancellation completed: response={result.get('response', '')[:50]}...")
    return cast(TransferState, result)
