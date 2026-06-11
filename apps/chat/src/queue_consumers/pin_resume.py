"""PIN verification resume handling for queue flow events."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.presentation.intents import map_outbox_to_intents
from banking.identity.repositories.user_repository import UserRepository
from banking.security.authorization import AuthorizationService
from shared.messaging.intents import UiIntent
from shared.messaging.outbox import enqueue_outbox_intents
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

TRANSACTION_PIN_FLOWS = {"transfer", "airtime", "data", "schedule"}
BATCH_PIN_FLOWS = {"batch", "transaction_batch", "transfer_batch", "multi_transfer", "mixed_batch"}
SUPPORTED_PIN_FLOWS = TRANSACTION_PIN_FLOWS | BATCH_PIN_FLOWS


def _normalize_flow_type(flow_type: str) -> str:
    return flow_type.strip().lower()


def _pin_flow_types_match(*, event_flow_type: str, recorded_flow_type: str) -> bool:
    if event_flow_type == recorded_flow_type:
        return True
    if event_flow_type in BATCH_PIN_FLOWS and recorded_flow_type in SUPPORTED_PIN_FLOWS:
        return True
    if recorded_flow_type in BATCH_PIN_FLOWS and event_flow_type in SUPPORTED_PIN_FLOWS:
        return True
    return False


def _resume_flow_type(*, event_flow_type: str, recorded_flow_type: str) -> str:
    if event_flow_type in BATCH_PIN_FLOWS and recorded_flow_type in TRANSACTION_PIN_FLOWS:
        return recorded_flow_type
    return event_flow_type


async def handle_pin_verified(
    *,
    flow_type: str,
    phone_number: str,
    idempotency_key: str,
    success: Any,
    channel: str,
    publisher: QueuePublisher,
    extra_data: dict[str, Any] | None = None,
    user_repository: UserRepository | None = None,
    orchestrator: OrchestratorAgent | None = None,
) -> None:
    """Resume a paused transaction after a successful PIN flow."""
    if success is not True:
        logger.warning(
            "pin_verified_but_not_success",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
        )
        return

    normalized_flow_type = _normalize_flow_type(flow_type)
    if normalized_flow_type not in SUPPORTED_PIN_FLOWS:
        logger.info(
            "pin_verified_non_transaction_flow_ignored",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
        )
        return

    if not phone_number or not idempotency_key:
        logger.warning(
            "pin_verified_missing_resume_context",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
            has_idempotency_key=bool(idempotency_key),
        )
        return

    if user_repository is None:
        logger.error(
            "pin_verified_user_repository_missing",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
        )
        return
    if orchestrator is None:
        logger.error(
            "pin_verified_orchestrator_missing",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
        )
        return

    authorization_service = AuthorizationService()
    auth_result = await authorization_service.get_pin_verification_result(idempotency_key)
    if not auth_result or not auth_result.verified or not auth_result.user_id:
        logger.warning(
            "pin_verified_resume_record_invalid",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
            has_record=bool(auth_result),
            verified=bool(auth_result.verified) if auth_result else False,
        )
        return

    recorded_flow_type = _normalize_flow_type(str(auth_result.transaction_type or ""))
    if not _pin_flow_types_match(event_flow_type=normalized_flow_type, recorded_flow_type=recorded_flow_type):
        logger.warning(
            "pin_verified_resume_flow_mismatch",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
            recorded_flow_type=recorded_flow_type,
        )
        return

    user = await user_repository.get_by_phone(phone_number)
    if not user or str(getattr(user, "id", "") or "") != str(auth_result.user_id):
        logger.warning(
            "pin_verified_resume_user_mismatch",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
        )
        return

    if not await authorization_service.claim_pin_resume(idempotency_key):
        logger.warning(
            "pin_verified_resume_replay_ignored",
            phone_hash=log_fingerprint(phone_number),
            flow_type=flow_type,
        )
        return

    logger.info(
        "resuming_via_orchestrator",
        phone_hash=log_fingerprint(phone_number),
        flow=flow_type,
        channel=channel,
    )
    response = await orchestrator.resume_transaction(
        phone_number=phone_number,
        flow_type=_resume_flow_type(event_flow_type=normalized_flow_type, recorded_flow_type=recorded_flow_type),
        pin_verified=True,
        idempotency_key=idempotency_key,
        authorized_user_id=str(auth_result.user_id),
        channel=channel,
    )

    if not response:
        return

    text = response.get("text") or response.get("final_response")
    outbox = response.get("outbox", [])
    raw_delivery_metadata = response.get("delivery_metadata")
    delivery_metadata = raw_delivery_metadata if isinstance(raw_delivery_metadata, dict) else {}

    raw_outbox = [item for item in outbox if isinstance(item, dict)] if isinstance(outbox, list) else []
    intents_to_send = cast(list[UiIntent | dict[str, Any]], map_outbox_to_intents(raw_outbox, text))

    if not intents_to_send:
        return

    outbox_phone = phone_number
    if extra_data and "chat_id" in extra_data:
        outbox_phone = str(extra_data["chat_id"])

    await enqueue_outbox_intents(
        publisher,
        outbox_phone,
        channel,
        intents_to_send,
        metadata={"source": "flow_event_handler", "flow_type": flow_type, **delivery_metadata},
    )
    logger.info(
        "pin_response_enqueued_outbox",
        outbox_phone_hash=log_fingerprint(outbox_phone),
        mapped_from_hash=log_fingerprint(phone_number),
    )
