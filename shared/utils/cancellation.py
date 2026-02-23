"""Shared cancellation utilities for all transaction flows (transfer, airtime, data and orchestrator)."""

from typing import Any, Literal

from shared.cache.redis_client import Redis, RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def cleanup_transaction_redis_keys(
    phone_number: str,
    transaction_type: Literal["transfer", "airtime", "data"],
    idempotency_key: str | None = None,
    redis_client: Redis | None = None,
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
        keys_to_delete.extend(
            [
                f"user:{phone_number}:pending_{transaction_type}",
                f"user:{phone_number}:pending_{transaction_type}_flow_token",
            ]
        )

    if phone_number and idempotency_key:
        if transaction_type == "transfer":
            keys_to_delete.extend(
                [
                    f"transfer:token:{idempotency_key}:phone",
                    f"transfer:retry:{idempotency_key}",
                    f"transfer:prev:{phone_number}:{idempotency_key}",
                ]
            )
        elif transaction_type == "airtime":
            keys_to_delete.extend(
                [
                    f"airtime:token:{idempotency_key}:phone",
                    f"airtime:retry:{idempotency_key}",
                    f"airtime:prev:{phone_number}:{idempotency_key}",
                ]
            )
        elif transaction_type == "data":
            keys_to_delete.extend(
                [
                    f"data:token:{idempotency_key}:phone",
                    f"data:retry:{idempotency_key}",
                    f"data:prev:{phone_number}:{idempotency_key}",
                ]
            )

    if keys_to_delete:
        try:
            deleted = await redis_client.delete(*keys_to_delete)
            logger.debug(
                "cancellation_redis_cleanup",
                transaction_type=transaction_type,
                deleted_count=deleted,
            )
            return deleted
        except Exception:
            logger.error("error_cleaning_up_redis")
            return 0

    return 0


def get_cancellation_message(
    transaction_type: Literal["transfer", "airtime", "data"],
    amount: float | None = None,
    recipient_name: str | None = None,
    phone_number: str | None = None,
) -> str:
    """Generate appropriate cancellation message based on transaction type."""
    if transaction_type == "transfer":
        if amount and recipient_name:
            return f"Transfer cancelled. The transfer of ₦{amount:,.0f} to {recipient_name} has been cancelled."
        elif amount:
            return f"Transfer cancelled. The transfer of ₦{amount:,.0f} has been cancelled."
        else:
            return "Transfer cancelled. The transaction has been cancelled."

    elif transaction_type == "airtime":
        if amount and phone_number:
            return f"Airtime purchase cancelled. The purchase of ₦{amount:,.0f} airtime for {phone_number} has been cancelled."
        elif amount:
            return f"Airtime purchase cancelled. The purchase of ₦{amount:,.0f} airtime has been cancelled."
        else:
            return "Airtime purchase cancelled. The transaction has been cancelled."

    elif transaction_type == "data":
        if amount and phone_number:
            return (
                f"Data purchase cancelled. The purchase of ₦{amount:,.0f} data for {phone_number} has been cancelled."
            )
        elif amount:
            return f"Data purchase cancelled. The purchase of ₦{amount:,.0f} data has been cancelled."
        else:
            return "Data purchase cancelled. The transaction has been cancelled."

    return "Transaction cancelled."


async def handle_transaction_cancellation(
    state: dict[str, Any],
    transaction_type: Literal["transfer", "airtime", "data"],
    redis_client: Redis | None = None,
) -> dict[str, Any]:
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
        recipient_phone = state.get("recipient_phone") or state.get("phone_number")

    await cleanup_transaction_redis_keys(
        phone_number=str(phone_number),
        transaction_type=transaction_type,
        idempotency_key=idem_key,
        redis_client=redis_client,
    )

    try:
        await redis_client.delete(f"user:{phone_number}:conversation_state")
        logger.info("cleared_for_cancelled")
    except Exception:
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
