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
                "flow_state": "collecting_recipient",
                "response": state.get("llm_reply") or f"I found multiple matches for '{rec_name}'. Which one? {opts}",
            }
        else:
            return {
                **state,
                "flow_state": "collecting_recipient",
                "response": state.get("llm_reply") or "Please provide the account number and bank name.",
            }

    if acct_number and not (bank_code or bank_name):
        return {
            **state,
            "flow_state": "collecting_recipient",
            "response": state.get("llm_reply") or "Please provide the bank name for this account.",
        }

    if not acct_number or not (bank_code or bank_name):
        return {
            **state,
            "flow_state": "collecting_recipient",
            "response": state.get("llm_reply") or "Please provide the account number and bank name.",
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

    # If bank_code exists but bank_name is provided, verify they match
    # This handles cases where bank_code was set incorrectly from checkpoint
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
            print(f"DEBUG validate_parallel: Bank cache not ready")
            return {
                **state,
                "flow_state": "validating",
            }

    if not bank_code:
        print(f"DEBUG validate_parallel: No bank_code available")
        return {
            **state,
            "flow_state": "error",
            "response": state.get("llm_reply") or "Bank code is required for validation. Please provide the bank name or code.",
            "validation_errors": ["missing_bank_code"],
        }

    if not acct_number:
        print(f"DEBUG validate_parallel: No account_number available")
        return {
            **state,
            "flow_state": "error",
            "response": state.get("llm_reply") or "Account number is required for validation.",
            "validation_errors": ["missing_account_number"],
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
    return {
        **state,
        "account_resolved": resolved,
        "balance_available": available,
        "flow_state": "validating",
        "validation_errors": [],  # Clear any previous validation errors
    }


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
