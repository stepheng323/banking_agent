"""Shared cancellation utilities for all transaction flows (transfer, airtime, data)."""

import json
from typing import Dict, Any, Literal, Optional, TYPE_CHECKING

from shared.cache.redis_client import RedisClient, Redis
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.models.classification import ClassificationResult


async def get_classification_result(phone_number: str) -> Optional["ClassificationResult"]:
    """
    Get the latest classification result from conversation state.

    Args:
        phone_number: User's phone number

    Returns:
        ClassificationResult if available, None otherwise
    """
    try:
        from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:last_classification"
        data = await redis_client.get(key)
        if data:
            result_dict = json.loads(data)
            return ClassificationResult.model_validate(result_dict)
    except Exception as e:
        logger.warning(f"Error retrieving classification result: {e}")
    return None


async def is_cancellation_intent(
    message: str,
    phone_number: Optional[str] = None,
) -> bool:
    """
    Check if user message indicates cancellation intent.
    Uses classification result from orchestrator if available, otherwise falls back to keyword detection.

    Args:
        message: User's message
        phone_number: Optional phone number to retrieve classification result
        context: Optional context (for fallback keyword detection)

    Returns:
        True if cancellation intent detected
    """
    if phone_number:
        classification_result = await get_classification_result(phone_number)
        if classification_result:
            is_cancel = (
                classification_result.intent.lower() == "cancel" or
                classification_result.is_cancellation is True
            )
            if is_cancel:
                logger.debug(
                    "cancellation_detected_llm",
                    intent=classification_result.intent,
                    confidence=classification_result.confidence
                )
                return True

    message_lower = message.lower().strip()
    cancellation_keywords = [
        "cancel", "abort", "stop", "nevermind", "never mind",
        "forget it", "forgetit", "don't send", "dont send",
        "don't do it", "dont do it", "ignore", "skip",
        "no thanks", "not now", "maybe later"
    ]
    keyword_match = any(
        keyword in message_lower for keyword in cancellation_keywords)
    if keyword_match:
        logger.debug("cancellation_detected_keyword")
    return keyword_match


async def cleanup_transaction_redis_keys(
    phone_number: str,
    transaction_type: Literal["transfer", "airtime", "data"],
    idempotency_key: Optional[str] = None,
    redis_client: Optional[Redis] = None,
) -> int:
    """
    Clean up all Redis keys related to a transaction.

    Args:
        phone_number: User's phone number
        transaction_type: Type of transaction (transfer, airtime, data)
        idempotency_key: Optional idempotency key for the transaction
        redis_client: Optional Redis client (uses default if not provided)

    Returns:
        Number of keys deleted
    """
    if not redis_client:
        redis_client = RedisClient.get_client()

    keys_to_delete = []

    if phone_number:
        keys_to_delete.extend([
            f"user:{phone_number}:pending_{transaction_type}",
            f"user:{phone_number}:pending_{transaction_type}_flow_token",
        ])

    if phone_number and idempotency_key:
        if transaction_type == "transfer":
            keys_to_delete.extend([
                f"transfer:token:{idempotency_key}:phone",
                f"transfer:retry:{idempotency_key}",
                f"transfer:prev:{phone_number}:{idempotency_key}",
            ])
        elif transaction_type == "airtime":
            keys_to_delete.extend([
                f"airtime:token:{idempotency_key}:phone",
                f"airtime:retry:{idempotency_key}",
                f"airtime:prev:{phone_number}:{idempotency_key}",
            ])
        elif transaction_type == "data":
            keys_to_delete.extend([
                f"data:token:{idempotency_key}:phone",
                f"data:retry:{idempotency_key}",
                f"data:prev:{phone_number}:{idempotency_key}",
            ])

    if keys_to_delete:
        try:
            deleted = await redis_client.delete(*keys_to_delete)
            logger.debug("cancellation_redis_cleanup", transaction_type=transaction_type, deleted_count=deleted)
            return deleted
        except Exception as e:
            logger.error("error_cleaning_up_redis")
            return 0

    return 0


def get_cancellation_message(
    transaction_type: Literal["transfer", "airtime", "data"],
    amount: Optional[float] = None,
    recipient_name: Optional[str] = None,
    phone_number: Optional[str] = None,
) -> str:
    """
    Generate appropriate cancellation message based on transaction type.

    Args:
        transaction_type: Type of transaction
        amount: Transaction amount (if available)
        recipient_name: Recipient name (for transfers)
        phone_number: Phone number (for airtime/data)

    Returns:
        Cancellation confirmation message
    """
    if transaction_type == "transfer":
        if amount and recipient_name:
            amount_str = f"₦{amount:,.0f}"
            if amount == int(amount):
                amount_str = amount_str.replace('.0', '')
            return f"Transfer cancelled. The transfer of {amount_str} to {recipient_name} has been cancelled."
        elif amount:
            amount_str = f"₦{amount:,.0f}"
            if amount == int(amount):
                amount_str = amount_str.replace('.0', '')
            return f"Transfer cancelled. The transfer of {amount_str} has been cancelled."
        else:
            return "Transfer cancelled. The transaction has been cancelled."

    elif transaction_type == "airtime":
        if amount and phone_number:
            amount_str = f"₦{amount:,.0f}"
            if amount == int(amount):
                amount_str = amount_str.replace('.0', '')
            return f"Airtime purchase cancelled. The purchase of {amount_str} airtime for {phone_number} has been cancelled."
        elif amount:
            amount_str = f"₦{amount:,.0f}"
            if amount == int(amount):
                amount_str = amount_str.replace('.0', '')
            return f"Airtime purchase cancelled. The purchase of {amount_str} airtime has been cancelled."
        else:
            return "Airtime purchase cancelled. The transaction has been cancelled."

    elif transaction_type == "data":
        if amount and phone_number:
            amount_str = f"₦{amount:,.0f}"
            if amount == int(amount):
                amount_str = amount_str.replace('.0', '')
            return f"Data purchase cancelled. The purchase of {amount_str} data for {phone_number} has been cancelled."
        elif amount:
            amount_str = f"₦{amount:,.0f}"
            if amount == int(amount):
                amount_str = amount_str.replace('.0', '')
            return f"Data purchase cancelled. The purchase of {amount_str} data has been cancelled."
        else:
            return "Data purchase cancelled. The transaction has been cancelled."


async def handle_transaction_cancellation(
    state: Dict[str, Any],
    transaction_type: Literal["transfer", "airtime", "data"],
    redis_client: Optional[Redis] = None,
) -> Dict[str, Any]:
    """
    Generic cancellation handler for any transaction type.

    Args:
        state: Transaction state dictionary (must have phone_number, active_flow, etc.)
        transaction_type: Type of transaction
        redis_client: Optional Redis client

    Returns:
        Updated state with cancellation applied
    """
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")
    amount = state.get("amount")

    if not redis_client:
        redis_client = RedisClient.get_client()

    recipient_name = None
    recipient_phone = None

    if transaction_type == "transfer":
        recipient_name = state.get("recipient_name")
    elif transaction_type in ("airtime", "data"):
        recipient_phone = state.get(
            "recipient_phone") or state.get("phone_number")

    await cleanup_transaction_redis_keys(
        phone_number=str(phone_number),
        transaction_type=transaction_type,
        idempotency_key=idem_key,
        redis_client=redis_client,
    )

    try:
        key = f"user:{phone_number}:conversation_state"
        await redis_client.delete(key)
        logger.info("cleared_for_cancelled")
    except Exception as e:
        logger.error("error_clearing")

    message = get_cancellation_message(
        transaction_type=transaction_type,
        amount=amount,
        recipient_name=recipient_name,
        phone_number=recipient_phone,
    )

    return {
        **state,
        "flow_state": "cancelled",
        "response": message,
        "transfer_status": None,
        "idempotency_key": None,
        "account_resolved": None,
        "matched_beneficiary": None,
        "selected_source_account": None,
        "amount": None,
        "recipient_account": None,
        "recipient_bank_code": None,
        "recipient_bank_name": None,
        "recipient_name": None,
        "validation_errors": [],
        "_change_acknowledged": False,
    }
