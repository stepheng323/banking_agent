"""Validation nodes for transfer flow."""

from typing import Any, cast

from apps.core.src.agent.tools.validation.service import AsyncValidationService
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.cache.bank_cache import BankCacheService

from .utils import debug_log
from shared.clients.whatsapp_client import WhatsAppClient
from apps.core.src.agent.sub_agents.transfer.validators import (
    SelfTransferValidator,
    BankCodeResolver,
    BeneficiaryMatcher,
    AccountValidator,
    ValidationCoordinator,
)


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
    """
    Parallel validation: resolve account + check balance.
    
    This function now delegates to ValidationCoordinator for better testability
    and maintainability while preserving the same interface.
    """
    self_transfer_validator = SelfTransferValidator()
    bank_code_resolver = BankCodeResolver(bank_cache)
    beneficiary_matcher = BeneficiaryMatcher()
    
    whatsapp_client = WhatsAppClient() if state.get("phone_number") else None
    account_validator = AccountValidator(validation_service, whatsapp_client)
    
    coordinator = ValidationCoordinator(
        self_transfer_validator=self_transfer_validator,
        bank_code_resolver=bank_code_resolver,
        beneficiary_matcher=beneficiary_matcher,
        account_validator=account_validator,
        bank_cache=bank_cache,
    )
    
    return await coordinator.validate(state, fetch_banks_func)
