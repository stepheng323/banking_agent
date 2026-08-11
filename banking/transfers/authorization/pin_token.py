"""PIN token persistence for transfer authorization flows."""

from typing import Any, Literal

from shared.utils.logging import get_logger

logger = get_logger(__name__)


PinFlowType = Literal["transfer", "schedule"]


async def persist_typed_pin_token(
    *,
    flow_type: PinFlowType,
    idempotency_key: str | None,
    phone_number: str,
    worker_context: Any,
) -> bool:
    """Persist the typed PIN flow lookup key used by webhook handlers."""
    if not idempotency_key:
        logger.warning("pin_token_missing_idempotency_key", flow_type=flow_type)
        return False

    try:
        redis_client = getattr(worker_context, "redis_client", None)
        if not redis_client:
            logger.debug("redis_client_not_in_context_skip_pin_token_persistence", flow_type=flow_type)
            return False

        await redis_client.setex(
            f"{flow_type}:token:{idempotency_key}:phone",
            3600,
            phone_number,
        )
        return True
    except Exception as exc:
        logger.error("failed_to_persist_pin_token", flow_type=flow_type, error=str(exc))
        return False


async def persist_transfer_pin_token(
    *,
    idempotency_key: str | None,
    phone_number: str,
    worker_context: Any,
) -> bool:
    """Persist the transfer PIN flow lookup key used by webhook handlers."""
    return await persist_typed_pin_token(
        flow_type="transfer",
        idempotency_key=idempotency_key,
        phone_number=phone_number,
        worker_context=worker_context,
    )


async def persist_schedule_pin_token(
    *,
    idempotency_key: str | None,
    phone_number: str,
    worker_context: Any,
) -> bool:
    """Persist the schedule PIN flow lookup key used by webhook handlers."""
    return await persist_typed_pin_token(
        flow_type="schedule",
        idempotency_key=idempotency_key,
        phone_number=phone_number,
        worker_context=worker_context,
    )
