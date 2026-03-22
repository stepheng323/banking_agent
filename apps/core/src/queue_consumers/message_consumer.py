"""Unified core consumer for chat messages and flow events."""

import time
from collections.abc import Callable
from typing import Any, cast

from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowOptions,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.messaging.outbox import enqueue_outbox_intents, enqueue_outbox_say
from shared.cache.rate_limiter import message_rate_limiter
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import ChannelMessage
from shared.queue.adapter import QueuePublisher
from shared.queue.messages import FlowEventType
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger
from shared.utils.sanitize import is_suspicious_input, sanitize_message

logger = get_logger(__name__)
_TRANSACTION_PIN_FLOWS = {"transfer", "airtime", "data"}
RuntimeBundleFactory = Callable[
    [],
    tuple[UserRepository, OnboardingExecutor, OrchestratorAgent],
]


class _NoopPublisher:
    async def publish(self, topic: Any, message: dict[str, Any]) -> None:
        del topic, message


class MessageConsumer:
    """Unified chat consumer used by the core ECS worker."""

    def __init__(
        self,
        user_repository: UserRepository | None,
        onboarding_executor: OnboardingExecutor | None,
        orchestrator: OrchestratorAgent | None,
        publisher: QueuePublisher | None = None,
        runtime_bundle_factory: RuntimeBundleFactory | None = None,
    ) -> None:
        self.publisher = publisher or _NoopPublisher()
        self.user_repository = user_repository
        self.onboarding_executor = onboarding_executor
        self.orchestrator = orchestrator
        self.runtime_bundle_factory = runtime_bundle_factory

    def _runtime_bundle(self) -> tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]:
        """Resolve runtime dependencies without a long-lived DB session."""
        if self.runtime_bundle_factory is None:
            if self.user_repository is None or self.onboarding_executor is None or self.orchestrator is None:
                raise RuntimeError("message_consumer_runtime_bundle_missing")
            return self.user_repository, self.onboarding_executor, self.orchestrator

        return self.runtime_bundle_factory()

    async def process_record(self, topic: str, payload: dict[str, Any]) -> None:
        """Route one transport payload by logical topic."""
        if topic == "message.received":
            await self.process_message(payload)
            return
        if topic == "flow_event.process":
            await self.process_flow_event(payload)
            return
        logger.warning("message_consumer_unknown_topic", topic=topic)

    async def process_message(self, message_data: dict[str, Any]) -> None:
        """Process one inbound chat message payload."""
        try:
            msg = ChannelMessage(**message_data)
            user_repository, onboarding_executor, orchestrator = self._runtime_bundle()
            await self._handle_message(
                msg,
                user_repository=user_repository,
                onboarding_executor=onboarding_executor,
                orchestrator=orchestrator,
            )
        except Exception as exc:
            logger.error("message_processing_failed", error=str(exc), exc_info=True)
            raise

    async def process_flow_event(self, event_data: dict[str, Any]) -> None:
        """Process one flow event payload."""
        try:
            _user_repository, _onboarding_executor, orchestrator = self._runtime_bundle()
            event_type = str(event_data.get("event_type", ""))
            flow_type = str(event_data.get("flow_type", ""))
            phone_number = str(event_data.get("phone_number", ""))
            idempotency_key = str(event_data.get("idempotency_key", ""))
            channel = str(event_data.get("channel", "whatsapp"))
            success = bool(event_data.get("success", False))
            extra_data_raw = event_data.get("extra_data")
            extra_data = extra_data_raw if isinstance(extra_data_raw, dict) else None

            logger.info(
                "flow_event_received",
                event_type=event_type,
                flow_type=flow_type,
                phone=phone_number,
                idem_key=idempotency_key,
            )

            if event_type == FlowEventType.PIN_VERIFIED.value:
                await self._handle_pin_verified(
                    flow_type=flow_type,
                    phone_number=phone_number,
                    success=success,
                    channel=channel,
                    extra_data=extra_data,
                    orchestrator=orchestrator,
                )
            elif event_type == FlowEventType.PIN_FAILED.value:
                logger.info("pin_verification_failed", phone=phone_number, flow_type=flow_type)
            else:
                logger.warning("unknown_flow_event", event_type=event_type, event_data=event_data)
        except Exception as exc:
            logger.error("flow_event_processing_failed", error=str(exc), event_data=event_data, exc_info=True)
            raise

    async def _handle_pin_verified(
        self,
        flow_type: str,
        phone_number: str,
        success: bool,
        channel: str,
        extra_data: dict[str, Any] | None = None,
        orchestrator: OrchestratorAgent | None = None,
    ) -> None:
        """Resume a paused transaction after a successful PIN flow."""
        runtime_orchestrator = orchestrator or self.orchestrator
        if not success:
            logger.warning("pin_verified_but_not_success", phone=phone_number, flow_type=flow_type)
            return

        normalized_flow_type = flow_type.strip().lower()
        if normalized_flow_type not in _TRANSACTION_PIN_FLOWS:
            logger.info("pin_verified_non_transaction_flow_ignored", phone=phone_number, flow_type=flow_type)
            return

        logger.info("resuming_via_orchestrator", phone=phone_number, flow=flow_type, channel=channel)
        response = await runtime_orchestrator.resume_transaction(
            phone_number=phone_number,
            flow_type=normalized_flow_type,
            pin_verified=True,
            channel=channel,
        )

        if not response:
            return

        text = response.get("text") or response.get("final_response")
        outbox = response.get("outbox", [])
        raw_delivery_metadata = response.get("delivery_metadata")
        delivery_metadata = raw_delivery_metadata if isinstance(raw_delivery_metadata, dict) else {}

        intents_to_send: list[UiIntent | dict[str, Any]] = []
        if text:
            intents_to_send.append({"type": "say", "text": text})
        if isinstance(outbox, list):
            intents_to_send.extend([item for item in outbox if isinstance(item, dict)])

        if not intents_to_send:
            return

        outbox_phone = phone_number
        if extra_data and "chat_id" in extra_data:
            outbox_phone = str(extra_data["chat_id"])

        await enqueue_outbox_intents(
            self.publisher,
            outbox_phone,
            channel,
            intents_to_send,
            metadata={"source": "flow_event_handler", "flow_type": flow_type, **delivery_metadata},
        )
        logger.info("pin_response_enqueued_outbox", outbox_phone=outbox_phone, mapped_from=phone_number)

    async def _handle_message(
        self,
        message: ChannelMessage,
        *,
        user_repository: UserRepository | None = None,
        onboarding_executor: OnboardingExecutor | None = None,
        orchestrator: OrchestratorAgent | None = None,
    ) -> dict[str, Any] | None:
        """Handle one channel message event."""
        start_time = time.perf_counter()
        channel_user_id = message.channel_user_id
        runtime_user_repository = user_repository or self.user_repository
        runtime_onboarding_executor = onboarding_executor or self.onboarding_executor
        runtime_orchestrator = orchestrator or self.orchestrator

        rate_result = await message_rate_limiter.check(channel_user_id)
        if not rate_result.allowed:
            logger.warning(
                "rate_limit_blocked",
                channel_user_id=channel_user_id,
                reset_in=rate_result.reset_in_seconds,
            )
            await enqueue_outbox_say(
                self.publisher,
                channel_user_id,
                message.channel,
                f"⏳ Too many messages. Please wait {rate_result.reset_in_seconds} seconds.",
                metadata={"source": "message_consumer", "reason": "rate_limit"},
            )
            return {"status": "rate_limited", "reset_in": rate_result.reset_in_seconds}

        raw_text = message.text or ""
        sanitized_text = sanitize_message(raw_text)

        if is_suspicious_input(sanitized_text):
            logger.warning(
                "suspicious_input_detected",
                channel_user_id=channel_user_id,
                text_preview=sanitized_text[:100],
            )

        if message.message_type.value == "flow":
            return {"status": "skipped", "reason": "flow_messages_handled_by_flow_ingress"}

        user = await runtime_user_repository.get_by_channel_identity(message.channel, channel_user_id)
        logger.info("channel_identity_lookup", user=user, channel=message.channel, channel_user_id=channel_user_id)

        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return cast(dict[str, Any] | None, await runtime_onboarding_executor.handle_onboarding(message))

        phone_number = str(user.phone_number)

        claimed_message = await runtime_orchestrator.context_manager.claim_inbound_message(
            phone_number,
            str(message.message_id),
        )
        if not claimed_message:
            logger.info("duplicate_inbound_message_ignored", phone_number=phone_number, message_id=message.message_id)
            return {"status": "duplicate_ignored", "message_id": message.message_id}

        response_text: str | None = None
        try:
            await runtime_orchestrator.context_manager.save_message_id(phone_number, str(message.message_id))

            orchestrator_output = await runtime_orchestrator.invoke(
                phone_number,
                sanitized_text,
                str(message.message_id),
                message_type=message.message_type.value,
                media_id=message.media_id,
                quoted_message_id=message.quoted_message_id,
                channel=message.channel,
                channel_identity=channel_user_id,
            )

            intents: list[UiIntent] = orchestrator_output.get("intents", [])
            response_text = orchestrator_output.get("text")
            delivery_metadata = (
                orchestrator_output.get("delivery_metadata")
                if isinstance(orchestrator_output.get("delivery_metadata"), dict)
                else {}
            )
            has_primary_interaction = any(
                isinstance(intent, (RequestAuth, RequestConfirmation, ShowReceipt, ShowOptions, ShowFlow))
                for intent in intents
            )
            if response_text and not has_primary_interaction and not any(isinstance(intent, Say) for intent in intents):
                intents.append(Say(text=response_text))

            if intents:
                await enqueue_outbox_intents(
                    self.publisher,
                    channel_user_id,
                    message.channel,
                    cast(list[UiIntent | dict[str, Any]], intents),
                    metadata={"source": "message_consumer", "message_id": message.message_id, **delivery_metadata},
                )
                logger.info("message_consumer_enqueued_outbox", count=len(intents))
        except Exception:
            await runtime_orchestrator.context_manager.release_inbound_message_claim(
                phone_number, str(message.message_id)
            )
            raise

        duration = (time.perf_counter() - start_time) * 1000
        logger.info(
            "perf_timer_latency",
            gate="message_consumer_handle",
            duration_ms=round(duration, 2),
            channel_user_id=channel_user_id,
        )
        return {"status": "success", "response": response_text}
