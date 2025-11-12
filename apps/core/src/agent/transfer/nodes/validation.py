"""Validation nodes for transfer flow."""

from typing import Any, cast
import hashlib
import json

from apps.core.src.agent.services.validation_service import AsyncValidationService
from apps.core.src.agent.transfer.state import TransferState
from shared.cache.bank_cache import BankCacheService

from .utils import debug_log


async def validate_amount(state: TransferState) -> TransferState:
    """Validate that amount is present."""
    if not state.get("amount"):
        return {
            **state,
            "flow_state": "collecting_amount",
            "response": state.get("llm_reply") or "How much should I send?",
        }
    return state


async def validate_parallel(
    state: TransferState,
    validation_service: AsyncValidationService,
    bank_cache: BankCacheService,
    fetch_banks_func: Any,
) -> TransferState:
    """Parallel validation: resolve account + check balance."""

    # SAFEGUARD: Check if recipient account AND bank match source account - only then is it an error
    # Note: Same account number can exist in different banks, so we must check BOTH account and bank
    recipient_account = state.get("recipient_account")
    recipient_bank_code = state.get("recipient_bank_code")
    recipient_bank_name = state.get("recipient_bank_name")
    selected_source_account = state.get("selected_source_account")

    if recipient_account and selected_source_account:
        source_account_number = selected_source_account.get(
            "account_number") or ""
        source_bank_name = selected_source_account.get("bank_name") or ""
        source_bank_code = selected_source_account.get("bank_code") or ""

        # Normalize bank names for comparison (case-insensitive)
        recipient_bank = (recipient_bank_name or "").lower().strip()
        source_bank = (source_bank_name or "").lower().strip()

        # Check if BOTH account number AND bank match (same account number in different banks is valid!)
        account_matches = recipient_account == source_account_number
        bank_matches = False
        if recipient_bank_code and source_bank_code:
            bank_matches = recipient_bank_code == source_bank_code
        elif recipient_bank and source_bank:
            # Compare bank names (normalized)
            bank_matches = recipient_bank == source_bank

        if account_matches and bank_matches:
            debug_log(
                f"🚨 ERROR in validate_parallel: Recipient account ({recipient_account}) and bank ({recipient_bank_name or recipient_bank_code}) match source account! This is wrong - clearing recipient data.")
            # Clear recipient data and set explicit error message
            error_message = (
                f"The recipient account ({recipient_account}) at {recipient_bank_name or recipient_bank_code or 'the same bank'} "
                f"cannot be the same as your source account. Please provide a different recipient account."
            )
            return {
                **state,
                "recipient_account": None,
                "recipient_bank_code": None,
                "recipient_bank_name": None,
                "recipient_name": None,
                "account_resolved": None,
                "matched_beneficiary": None,
                "validation_errors": ["Recipient account and bank cannot be the same as source account"],
                "flow_state": "collecting_recipient",
                "response": error_message,
                "llm_reply": None,  # Clear llm_reply to prevent showing partial confirmation
            }
        elif account_matches and not bank_matches:
            # Account number matches but bank is different - this is VALID (same number in different banks)
            debug_log(
                f"ℹ️ validate_parallel: Account number ({recipient_account}) matches source, but bank differs. This is valid - same account number in different banks.")

    if not validation_service:
        return state

    source = state.get("selected_source_account")
    if not source or not source.get("id"):
        return state

    acct_number = state.get("recipient_account")
    bank_code = state.get("recipient_bank_code")
    bank_name = state.get("recipient_bank_name")

    # OPTIMIZED: Single bank cache check (removed duplicate)
    # Account normalization already done in extract_entities, skip here
    if bank_cache and bank_name:
        cache_ready = await bank_cache.ensure_banks_cached(fetch_banks_func)
        if cache_ready:
            resolved_code = await bank_cache.get_bank_code(bank_name)
            if resolved_code:
                # Update bank_code if missing or correct if mismatch
                if not bank_code or bank_code != resolved_code:
                    bank_code = resolved_code
                    state = {
                        **state,
                        "recipient_bank_code": resolved_code,
                    }
            elif not bank_code:
                # No bank code found and none provided
                return {
                    **state,
                    "flow_state": "error",
                    "response": state.get("llm_reply") or f"I couldn't find a bank code for '{bank_name}'. Please provide the bank name or code.",
                    "validation_errors": ["bank_code_resolution_failed"],
                }
        else:
            # Cache not ready - return to retry
            return {
                **state,
                "flow_state": "validating",
            }

    if not bank_code:
        debug_log("DEBUG validate_parallel: No bank_code available")
        return {
            **state,
            "flow_state": "error",
            "response": state.get("llm_reply") or "Bank code is required for validation. Please provide the bank name or code.",
            "validation_errors": ["missing_bank_code"],
        }

    if not acct_number:
        debug_log("DEBUG validate_parallel: No account_number available")
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
            debug_log(
                f"DEBUG validate_parallel: Valid beneficiary match found (account={current_account}, bank={current_bank_code})")
        else:
            # Matched beneficiary doesn't match current recipient - it's stale!
            debug_log(f"DEBUG validate_parallel: Stale matched_beneficiary detected. "
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
                debug_log(
                    f"DEBUG validate_parallel: Reusing existing account_resolved for beneficiary")
                resolved = account_resolved
                balance = None
            else:
                # Existing resolution doesn't match - reconstruct from beneficiary
                debug_log(
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
            debug_log(
                f"DEBUG validate_parallel: Using beneficiary data from database")
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

        debug_log(
            f"DEBUG validate_parallel: Calling validation API with account_number='{acct_number}', bank_code='{bank_code}'")
        resolved, balance = await validation_service.validate_account_and_balance(
            account_number=str(acct_number),
            bank_code=str(bank_code),
            source_account_id=str(source.get("id")),
        )
        debug_log(
            f"DEBUG validate_parallel: Validation result - resolved={resolved is not None}, balance={balance is not None}")
        if resolved:
            debug_log(
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
            debug_log(f"⚠️  Balance check failed (non-critical): {e}")
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

