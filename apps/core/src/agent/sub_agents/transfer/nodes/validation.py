"""Validation nodes for transfer flow."""

from typing import Any, Optional

from apps.core.src.agent.tools.validation.service import AsyncValidationService
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from apps.core.src.agent.orchestrator.features.response import (
    ResponseIntent,
    ResponseContext,
    build_response_context,
    get_synthesizer,
)
from shared.cache.bank_cache import BankCacheService
from shared.clients.whatsapp_client import WhatsAppClient

from .utils import debug_log
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
        context = build_response_context(ResponseIntent.ASK_AMOUNT, state)
        synthesizer = get_synthesizer()
        response = await synthesizer.synthesize(context)
        
        return {
            **state,
            "flow_state": "collecting_amount",
            "response": response,
        }
    return state


async def validate_parallel(
    state: TransferState,
    validation_service: AsyncValidationService,
    bank_cache: BankCacheService,
    fetch_banks_func: Any,
    whatsapp_client: Optional[WhatsAppClient] = None,
) -> TransferState:
    """
    Parallel validation: resolve account + check balance.
    
    Args:
        state: Current transfer state
        validation_service: Service for account validation
        bank_cache: Bank cache service
        fetch_banks_func: Function to fetch banks
        whatsapp_client: Optional WhatsApp client for sending acknowledgments
    """
    self_transfer_validator = SelfTransferValidator()
    bank_code_resolver = BankCodeResolver(bank_cache)
    beneficiary_matcher = BeneficiaryMatcher()
    account_validator = AccountValidator(validation_service, whatsapp_client)
    
    coordinator = ValidationCoordinator(
        self_transfer_validator=self_transfer_validator,
        bank_code_resolver=bank_code_resolver,
        beneficiary_matcher=beneficiary_matcher,
        account_validator=account_validator,
        bank_cache=bank_cache,
    )
    
    return await coordinator.validate(state, fetch_banks_func)

