"""Flow event routing for the chat message consumer."""

from typing import Any

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.queue_consumers.pin_resume import handle_pin_verified
from banking.identity.repositories.user_repository import UserRepository
from shared.queue.adapter import QueuePublisher
from shared.queue.messages import FlowEventType
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


async def process_flow_event_payload(
    *,
    event_data: dict[str, Any],
    user_repository: UserRepository,
    orchestrator: OrchestratorAgent,
    publisher: QueuePublisher,
) -> None:
    """Process one flow event payload."""
    event_type = str(event_data.get("event_type") or "")
    flow_type = str(event_data.get("flow_type") or "")
    phone_number = str(event_data.get("phone_number") or "")
    idempotency_key = str(event_data.get("idempotency_key") or "")
    channel = str(event_data.get("channel") or "whatsapp")
    success = event_data.get("success", False)
    extra_data_raw = event_data.get("extra_data")
    extra_data = extra_data_raw if isinstance(extra_data_raw, dict) else None

    logger.info(
        "flow_event_received",
        event_type=event_type,
        flow_type=flow_type,
        phone_hash=log_fingerprint(phone_number),
        idempotency_key_hash=log_fingerprint(idempotency_key),
        extra_data_keys=sorted(str(key) for key in extra_data) if extra_data else [],
    )

    if event_type == FlowEventType.PIN_VERIFIED.value:
        await handle_pin_verified(
            flow_type=flow_type,
            phone_number=phone_number,
            idempotency_key=idempotency_key,
            success=success,
            channel=channel,
            publisher=publisher,
            extra_data=extra_data,
            user_repository=user_repository,
            orchestrator=orchestrator,
        )
        return

    if event_type == FlowEventType.PIN_FAILED.value:
        logger.info("pin_verification_failed", phone_hash=log_fingerprint(phone_number), flow_type=flow_type)
        return

    logger.warning("unknown_flow_event", event_type=event_type, event_keys=sorted(event_data.keys()))
