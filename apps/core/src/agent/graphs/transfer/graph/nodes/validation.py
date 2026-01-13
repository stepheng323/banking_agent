"""Validation nodes for transfer flow."""

from typing import Any, cast

from apps.core.src.agent.graphs.__shared__.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    TRANSFER_LIMITS,
    validate_percentage,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.__shared__.validation.service import AsyncValidationService
from apps.core.src.agent.graphs.transfer.state import TransferState
from apps.core.src.agent.graphs.transfer.validators import (
    AccountValidator,
    BankCodeResolver,
    BeneficiaryMatcher,
    SelfTransferValidator,
    ValidationCoordinator,
)
from shared.cache.bank_cache import BankCacheService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def validate_amount(state: TransferState) -> TransferState:
    """Validate that amount is present and within acceptable limits."""
    amount = state.get("amount")
    transfer_all = state.get("transfer_all")
    transfer_percentage = state.get("transfer_percentage")
    synthesizer = get_synthesizer()

    logger.info(
        "validate_amount_entry",
        amount=amount,
        has_amount=bool(amount),
        transfer_all=transfer_all,
        transfer_percentage=transfer_percentage,
        flow_state=state.get("flow_state"),
    )

    if transfer_percentage:
        is_valid, error_msg, validated_pct = validate_percentage(transfer_percentage)
        if not is_valid:
            logger.warning("validate_amount_invalid_percentage", percentage=transfer_percentage, error=error_msg)
            context = build_response_context(
                ResponseIntent.INVALID_AMOUNT,
                state,
                error_message=error_msg,
            )
            response = await synthesizer.synthesize(context)
            return cast(
                TransferState,
                {
                    **state,
                    "flow_state": "error",
                    "response": response,
                    "validation_errors": ["invalid_percentage"],
                },
            )
        logger.info("validate_amount_skip_percentage", percentage=validated_pct)
        return cast(TransferState, {**state, "transfer_percentage": validated_pct})

    if transfer_all:
        logger.info("validate_amount_skip_transfer_all")
        return state

    if not amount:
        context = build_response_context(ResponseIntent.ASK_AMOUNT, state)
        response = await synthesizer.synthesize(context)

        return cast(
            TransferState,
            {
                **state,
                "flow_state": "collecting_amount",
                "response": response,
            },
        )

    is_valid, error_msg, validated_amount = validate_amount_limits(amount, TRANSFER_LIMITS)
    if not is_valid:
        logger.warning("validate_amount_invalid", amount=amount, error=error_msg)
        context = build_response_context(
            ResponseIntent.INVALID_AMOUNT,
            state,
            error_message=error_msg,
        )
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "flow_state": "error",
                "response": response,
                "validation_errors": ["invalid_amount"],
            },
        )

    logger.info("validate_amount_valid", amount=validated_amount)
    return cast(TransferState, {**state, "amount": validated_amount})


async def validate_parallel(
    state: TransferState,
    validation_service: AsyncValidationService,
    bank_cache: BankCacheService,
    fetch_banks_func: Any,
    whatsapp_client: WhatsAppClient | None = None,
) -> TransferState:
    """
    Parallel validation: resolve bank code + validate recipient account.

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
