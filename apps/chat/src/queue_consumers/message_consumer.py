"""Unified core consumer for chat messages and flow events."""

import time
from collections.abc import Callable
from typing import Any, cast

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.queue_consumers.channel_link_gate import gate_unlinked_whatsapp_identity
from apps.chat.src.queue_consumers.flow_events import process_flow_event_payload
from apps.chat.src.queue_consumers.message_inbound import (
    record_latest_inbound_for_delivery_target,
    resolve_channel_user,
    resolve_latest_inbound_redis_client,
)
from apps.chat.src.queue_consumers.message_outbound import (
    is_pending_input_prompt_outbox,
    log_prepared_outbound,
    prepare_orchestrator_outbound,
    should_suppress_intermediate_input_prompt,
    suppress_spurious_greeting_intents,
)
from apps.chat.src.queue_consumers.receipt_choices import handle_receipt_image_choice
from banking.accounts.onboarding.executor import OnboardingExecutor
from banking.identity.repositories.user_repository import UserRepository
from banking.presentation.i18n.renderer import render_message
from shared.cache.distributed_lock import RedisLockTimeoutError
from shared.cache.rate_limiter import message_rate_limiter
from shared.clients.telegram.client import TelegramClient
from shared.database.models import UserOnboardingStatusEnum
from shared.messaging.intents import Say
from shared.messaging.outbox import enqueue_outbox_intents, enqueue_outbox_say
from shared.messaging.prompt_suppression import pending_input_prompt_metadata
from shared.models.messages import ChannelMessage
from shared.observability.events import emit_operational_event
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger, log_fingerprint
from shared.utils.sanitize import is_suspicious_input, sanitize_message

logger = get_logger(__name__)
_SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY = "_suppress_intermediate_input_prompt"
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
        latest_inbound_redis_client: Any | None = None,
        telegram_client_factory: Callable[[], TelegramClient] | None = None,
    ) -> None:
        self.publisher = publisher or _NoopPublisher()
        self.user_repository = user_repository
        self.onboarding_executor = onboarding_executor
        self.orchestrator = orchestrator
        self.runtime_bundle_factory = runtime_bundle_factory
        self.latest_inbound_redis_client = latest_inbound_redis_client
        self.telegram_client_factory = telegram_client_factory or TelegramClient

    def _runtime_bundle(self) -> tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]:
        """Resolve runtime dependencies without a long-lived DB session."""
        if self.runtime_bundle_factory is None:
            if self.user_repository is None or self.onboarding_executor is None or self.orchestrator is None:
                raise RuntimeError("message_consumer_runtime_bundle_missing")
            return self.user_repository, self.onboarding_executor, self.orchestrator

        return self.runtime_bundle_factory()

    @staticmethod
    def _log_latency_span(
        *,
        span: str,
        duration_ms: float,
        channel_user_id: str,
        phone_number: str | None = None,
    ) -> None:
        logger.info(
            "perf_timer_latency",
            gate=span,
            duration_ms=round(duration_ms, 2),
            channel_user_id=channel_user_id,
            phone_number=phone_number,
        )

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
            user_repository, _onboarding_executor, orchestrator = self._runtime_bundle()
            await process_flow_event_payload(
                event_data=event_data,
                user_repository=user_repository,
                orchestrator=orchestrator,
                publisher=self.publisher,
            )
        except Exception as exc:
            logger.error(
                "flow_event_processing_failed",
                error_type=type(exc).__name__,
                event_keys=sorted(event_data.keys()),
                exc_info=True,
            )
            raise

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
        if runtime_user_repository is None:
            raise RuntimeError("user_repository_unavailable")
        if runtime_onboarding_executor is None:
            raise RuntimeError("onboarding_executor_unavailable")
        if runtime_orchestrator is None:
            raise RuntimeError("orchestrator_unavailable")

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

        identity_start = time.perf_counter()
        user = await resolve_channel_user(
            user_repository=runtime_user_repository,
            channel=message.channel,
            channel_user_id=channel_user_id,
        )
        self._log_latency_span(
            span="message_consumer_identity_lookup",
            duration_ms=(time.perf_counter() - identity_start) * 1000,
            channel_user_id=channel_user_id,
        )
        logger.info("channel_identity_lookup", user=user, channel=message.channel, channel_user_id=channel_user_id)

        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return cast(dict[str, Any] | None, await runtime_onboarding_executor.handle_onboarding(message))

        link_gate_result = await gate_unlinked_whatsapp_identity(
            message=message,
            user=user,
            user_repository=runtime_user_repository,
            publisher=self.publisher,
            telegram_client_factory=self.telegram_client_factory,
        )
        if link_gate_result is not None:
            return link_gate_result

        phone_number = str(user.phone_number)

        claim_start = time.perf_counter()
        claimed_message = await runtime_orchestrator.context_manager.claim_inbound_message(
            phone_number,
            str(message.message_id),
        )
        self._log_latency_span(
            span="message_consumer_dedupe_claim",
            duration_ms=(time.perf_counter() - claim_start) * 1000,
            channel_user_id=channel_user_id,
            phone_number=phone_number,
        )
        if not claimed_message:
            logger.info(
                "duplicate_inbound_message_ignored",
                phone_hash=log_fingerprint(phone_number),
                message_id_hash=log_fingerprint(message.message_id),
            )
            return {"status": "duplicate_ignored", "message_id": message.message_id}

        response_text: str | None = None
        try:
            save_start = time.perf_counter()
            await runtime_orchestrator.context_manager.save_message_id(phone_number, str(message.message_id))
            self._log_latency_span(
                span="message_consumer_message_id_save",
                duration_ms=(time.perf_counter() - save_start) * 1000,
                channel_user_id=channel_user_id,
                phone_number=phone_number,
            )
            await record_latest_inbound_for_delivery_target(
                configured_redis_client=self.latest_inbound_redis_client,
                orchestrator=runtime_orchestrator,
                channel=message.channel,
                delivery_target=channel_user_id,
                message_id=str(message.message_id),
            )

            receipt_choice_result = await handle_receipt_image_choice(
                message=message,
                orchestrator=runtime_orchestrator,
                user=user,
                phone_number=phone_number,
                channel_user_id=channel_user_id,
                text=sanitized_text,
                publisher=self.publisher,
                redis_client=resolve_latest_inbound_redis_client(
                    configured_redis_client=self.latest_inbound_redis_client,
                    orchestrator=runtime_orchestrator,
                ),
                telegram_client_factory=self.telegram_client_factory,
            )
            if receipt_choice_result is not None:
                return receipt_choice_result

            invoke_start = time.perf_counter()
            try:
                orchestrator_output = await runtime_orchestrator.invoke(
                    phone_number,
                    sanitized_text,
                    str(message.message_id),
                    message_type=message.message_type.value,
                    media_id=message.media_id,
                    mime_type=message.mime_type,
                    quoted_message_id=message.quoted_message_id,
                    channel=message.channel,
                    channel_identity=channel_user_id,
                    channel_metadata=message.channel_metadata,
                    user=user,
                )
            except Exception as exc:
                if isinstance(exc, RedisLockTimeoutError):
                    logger.warning(
                        "message_consumer_orchestrator_lock_timeout",
                        phone_number=phone_number,
                        message_id=message.message_id,
                    )
                    raise
                logger.error(
                    "message_consumer_orchestrator_invoke_failed_safe_fallback",
                    error=str(exc),
                    phone_number=phone_number,
                    message_id=message.message_id,
                    exc_info=True,
                )
                emit_operational_event(
                    "llm_orchestrator_safe_fallback",
                    severity="high",
                    domain="llm",
                    identifiers={
                        "phone_number": phone_number,
                        "channel_user_id": channel_user_id,
                        "message_id": message.message_id,
                    },
                    details={"error_type": type(exc).__name__, "channel": message.channel},
                )
                response_text = render_message("orchestrator.fallback.processing_error", "en")
                await enqueue_outbox_intents(
                    self.publisher,
                    channel_user_id,
                    message.channel,
                    [Say(text=response_text)],
                    metadata={"source": "message_consumer", "message_id": message.message_id, "safe_fallback": True},
                )
                return {"status": "safe_fallback", "response": response_text}
            self._log_latency_span(
                span="message_consumer_orchestrator_invoke",
                duration_ms=(time.perf_counter() - invoke_start) * 1000,
                channel_user_id=channel_user_id,
                phone_number=phone_number,
            )

            intents_to_send, raw_outbox, response_text, delivery_metadata = prepare_orchestrator_outbound(
                orchestrator_output,
                message_id=str(message.message_id),
            )

            if intents_to_send:
                if should_suppress_intermediate_input_prompt(
                    message=message,
                    raw_outbox=raw_outbox,
                    metadata_key=_SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY,
                ):
                    logger.info(
                        "message_consumer_intermediate_input_prompt_suppressed",
                        message_id=message.message_id,
                        channel=message.channel,
                        channel_user_id=channel_user_id,
                    )
                    intents_to_send = []
            if intents_to_send:
                intents_to_send = suppress_spurious_greeting_intents(intents_to_send)
                log_prepared_outbound(
                    message=message,
                    phone_number=phone_number,
                    response_text=response_text,
                    intents=intents_to_send,
                )
            if intents_to_send:
                outbound_metadata: dict[str, Any] = {
                    "source": "message_consumer",
                    "message_id": message.message_id,
                    **delivery_metadata,
                }
                if is_pending_input_prompt_outbox(raw_outbox):
                    outbound_metadata.update(
                        pending_input_prompt_metadata(
                            channel=message.channel,
                            delivery_target=channel_user_id,
                            origin_message_id=str(message.message_id),
                        )
                    )
                outbox_start = time.perf_counter()
                await enqueue_outbox_intents(
                    self.publisher,
                    channel_user_id,
                    message.channel,
                    intents_to_send,
                    metadata=outbound_metadata,
                )
                self._log_latency_span(
                    span="message_consumer_outbox_enqueue",
                    duration_ms=(time.perf_counter() - outbox_start) * 1000,
                    channel_user_id=channel_user_id,
                    phone_number=phone_number,
                )
                logger.info("message_consumer_enqueued_outbox", count=len(intents_to_send))
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
