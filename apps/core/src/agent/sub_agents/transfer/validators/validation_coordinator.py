"""Validation coordinator - orchestrates all validation steps."""

import hashlib
import json
from typing import Any, Optional

from apps.core.src.agent.tools.account_selection.service import AccountSelectionService
from apps.core.src.agent.sub_agents.transfer.state import TransferState

from shared.cache.bank_cache import BankCacheService
from shared.utils.logging import get_logger

from .self_transfer_validator import SelfTransferValidator
from .bank_code_resolver import BankCodeResolver
from .beneficiary_matcher import BeneficiaryMatcher
from .account_validator import AccountValidator


logger = get_logger(__name__)


class ValidationCoordinator:
    """Coordinates all validation steps for transfer flow."""

    def __init__(
        self,
        self_transfer_validator: SelfTransferValidator,
        bank_code_resolver: BankCodeResolver,
        beneficiary_matcher: BeneficiaryMatcher,
        account_validator: AccountValidator,
        bank_cache: BankCacheService,
    ):
        """
        Initialize coordinator.

        Args:
            self_transfer_validator: Validator for self-transfer checks
            bank_code_resolver: Resolver for bank codes
            beneficiary_matcher: Matcher for beneficiaries
            account_validator: Validator for account resolution
            bank_cache: Bank cache service for Redis operations
        """
        self.self_transfer_validator = self_transfer_validator
        self.bank_code_resolver = bank_code_resolver
        self.beneficiary_matcher = beneficiary_matcher
        self.account_validator = account_validator
        self.bank_cache = bank_cache

    async def validate(
        self,
        state: TransferState,
        fetch_banks_func: Any,
    ) -> TransferState:
        """
        Orchestrate all validation steps.

        Args:
            state: Current transfer state
            fetch_banks_func: Function to fetch banks if cache is empty

        Returns:
            Updated transfer state
        """
        is_internal_transfer = state.get("is_internal_transfer", False)
        if is_internal_transfer:
            state = await self._resolve_internal_transfer(state)
            if state.get("flow_state") == "error":
                return state

        recipient_account = state.get("recipient_account")
        recipient_bank_code = state.get("recipient_bank_code")
        recipient_bank_name = state.get("recipient_bank_name")
        selected_source_account = state.get("selected_source_account")
        phone_number = state.get("phone_number")

        is_valid, error_message = self.self_transfer_validator.validate(
            recipient_account=recipient_account,
            recipient_bank_code=recipient_bank_code,
            recipient_bank_name=recipient_bank_name,
            source_account=selected_source_account,
        )

        if not is_valid:
            return {
                **state,
                "recipient_account": None,
                "recipient_bank_code": None,
                "recipient_bank_name": None,
                "recipient_name": None,
                "account_resolved": None,
                "matched_beneficiary": None,
                "validation_errors": ["self_transfer_detected"],
                "flow_state": "collecting_recipient",
                "response": error_message,
                "llm_reply": None,
            }

        if recipient_bank_name and not recipient_bank_code:
            resolved_code = await self.bank_code_resolver.resolve(
                bank_name=recipient_bank_name,
                fetch_banks_func=fetch_banks_func,
            )

            if resolved_code:
                recipient_bank_code = resolved_code
                state = {
                    **state,
                    "recipient_bank_code": resolved_code,
                }
            else:
                return {
                    **state,
                    "flow_state": "error",
                    "response": state.get("llm_reply") or f"I couldn't find a bank code for '{recipient_bank_name}'. Please provide the bank name or code.",
                    "validation_errors": ["bank_code_resolution_failed"],
                }

        if not recipient_bank_code:
            return {
                **state,
                "flow_state": "error",
                "response": state.get("llm_reply") or "Bank code is required for validation. Please provide the bank name or code.",
                "validation_errors": ["missing_bank_code"],
            }

        if not recipient_account:
            return {
                **state,
                "flow_state": "error",
                "response": state.get("llm_reply") or "Account number is required for validation.",
                "validation_errors": ["missing_account_number"],
            }

        if not selected_source_account or not selected_source_account.get("id"):
            return state

        matched_beneficiary = state.get("matched_beneficiary")
        use_beneficiary, resolved_account = self.beneficiary_matcher.match(
            matched_beneficiary=matched_beneficiary,
            current_account=recipient_account,
            current_bank_code=recipient_bank_code,
            current_recipient_name=state.get("recipient_name"),
        )

        if use_beneficiary and resolved_account:
            logger.info("using_beneficiary_data", account=recipient_account)
            resolved = resolved_account
            balance = None
        else:
            if matched_beneficiary and not use_beneficiary:
                state = {
                    **state,
                    "matched_beneficiary": None,
                }

            resolved, balance = await self.account_validator.validate(
                account_number=recipient_account,
                bank_code=recipient_bank_code,
                source_account_id=str(selected_source_account.get("id")),
                phone_number=phone_number,
            )

            if resolved is None or (isinstance(resolved, dict) and not resolved.get("success", False)):
                return {
                    **state,
                    "flow_state": "error",
                    "response": state.get("llm_reply") or "I couldn't verify that account right now. Please confirm the account number and bank.",
                    "validation_errors": ["account_resolution_failed"],
                }

        available = None
        if balance:
            try:
                available = float(balance.get("available", 0))
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
                logger.warning("balance_check_error", error=str(e))
                available = None

        idem_key = state.get("idempotency_key")
        if not idem_key:
            idem_key = hashlib.sha256(
                f"{phone_number}|{state.get('amount')}|{recipient_account}|{recipient_bank_name}".encode("utf-8")
            ).hexdigest()

        if phone_number and idem_key:
            prev_key = f"transfer:prev:{phone_number}:{idem_key}"
            prev_values = {
                "amount": state.get("amount"),
                "recipient_account": recipient_account,
                "recipient_bank_code": recipient_bank_code,
                "recipient_bank_name": recipient_bank_name,
                "recipient_name": resolved.get("account_name") if isinstance(resolved, dict) else state.get("recipient_name"),
            }
            await self.bank_cache.redis.set(
                prev_key,
                json.dumps(prev_values),
                ex=3600  # 1 hour expiry
            )

        # Extract recipient_name for the state
        resolved_recipient_name = resolved.get("account_name") if isinstance(resolved, dict) else state.get("recipient_name")

        return {
            **state,
            "account_resolved": resolved,
            "balance_available": available,
            "flow_state": "validating",
            "validation_errors": [],
            "idempotency_key": idem_key,
            "recipient_name": resolved_recipient_name,  # Ensure recipient_name is in state for authorization
            "recipient_bank_code": recipient_bank_code,  # Ensure bank_code is persisted
        }

    async def _resolve_internal_transfer(self, state: TransferState) -> TransferState:
        """
        Resolve internal transfer destination by finding user's account by bank name.
        
        For internal transfers, the recipient is the user's own account at the destination bank.
        This method finds that account and populates the recipient fields.
        
        Args:
            state: Current transfer state with is_internal_transfer=True
            
        Returns:
            Updated state with recipient_account and recipient_bank populated from user's account
        """
        
        accounts = state.get("accounts", [])
        recipient_bank_name = state.get("recipient_bank_name")
        selected_source_account = state.get("selected_source_account")
        
        if not recipient_bank_name:
            return {
                **state,
                "flow_state": "error",
                "response": "Please specify which account to transfer to (e.g., 'to my GTB').",
                "validation_errors": ["missing_destination_bank"],
            }
        
        destination_account = AccountSelectionService.find_account_by_bank_name(
            accounts, recipient_bank_name
        )
        
        if not destination_account:
            return {
                **state,
                "flow_state": "error",
                "response": f"I couldn't find a {recipient_bank_name} account linked to your profile. Please link it first or check the bank name.",
                "validation_errors": ["destination_account_not_found"],
            }
        
        if selected_source_account:
            is_valid, error_message = self.self_transfer_validator.validate(
                recipient_account=destination_account.get("account_number"),
                recipient_bank_code=destination_account.get("bank_code"),
                recipient_bank_name=destination_account.get("bank_name"),
                source_account=selected_source_account,
            )
            if not is_valid:
                return {
                    **state,
                    "flow_state": "error",
                    "response": error_message or "Source and destination accounts are the same. Please specify different accounts.",
                    "validation_errors": ["same_source_destination"],
                }
        
        logger.info(
            "internal_transfer_resolved",
            source_bank=selected_source_account.get("bank_name") if selected_source_account else None,
            destination_bank=destination_account.get("bank_name"),
            destination_account=destination_account.get("account_number"),
        )
        
        return {
            **state,
            "recipient_account": destination_account.get("account_number"),
            "recipient_bank_name": destination_account.get("bank_name"),
            "recipient_bank_code": destination_account.get("bank_code"),
            "recipient_name": destination_account.get("account_name"),
            "account_resolved": {
                "success": True,
                "account_name": destination_account.get("account_name"),
                "account_number": destination_account.get("account_number"),
                "bank_code": destination_account.get("bank_code"),
                "provider": "internal",
            },
        }
