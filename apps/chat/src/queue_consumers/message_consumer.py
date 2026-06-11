"""Unified core consumer for chat messages and flow events."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
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
from apps.chat.src.runtime.bundles import ChatRuntimeBundle, ChatRuntimeBundleFactory
from banking.accounts.onboarding.executor import OnboardingExecutor
from banking.identity.repositories.user_repository import UserRepository
from banking.presentation.i18n.renderer import render_message
from shared.cache.distributed_lock import RedisLockTimeoutError
from shared.cache.rate_limiter import message_rate_limiter
from shared.clients.telegram.client import TelegramClient
from shared.config.settings import settings
from shared.database.models import UserOnboardingStatusEnum
from shared.messaging.intents import Say, SendTyping
from shared.messaging.outbox import enqueue_outbox_intents, enqueue_outbox_say
from shared.messaging.prompt_suppression import pending_input_prompt_metadata
from shared.models.messages import ChannelMessage
from shared.observability.events import emit_operational_event
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger, log_fingerprint
from shared.utils.sanitize import is_suspicious_input, sanitize_message

logger = get_logger(__name__)
_SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY = "_suppress_intermediate_input_prompt"
_INITIAL_TYPING_DELAY_SECONDS = 0.0
_INITIAL_TYPING_POLICY = "delayed_initial"
_HEARTBEAT_TYPING_POLICY = "heartbeat"
_FINAL_PREFACE_TYPING_POLICY = "final_preface"


class _NoopPublisher:
    async def publish(self, topic: Any, message: dict[str, Any]) -> None:
        del topic, message


def _initial_typing_dedupe_key(*, channel: str, delivery_target: str, message_id: str) -> str:
    return f"{channel}:{delivery_target}:{message_id}:typing:initial"


def _heartbeat_typing_dedupe_key(*, channel: str, delivery_target: str, message_id: str, sequence: int) -> str:
    return f"{channel}:{delivery_target}:{message_id}:typing:heartbeat:{sequence}"


def _final_preface_typing_dedupe_key(*, channel: str, delivery_target: str, message_id: str) -> str:
    return f"{channel}:{delivery_target}:{message_id}:typing:final_preface"


def _is_typing_intent(intent: Any) -> bool:
    if isinstance(intent, SendTyping):
        return True
    if isinstance(intent, dict):
        return intent.get("type") == "typing"
    return False


def _has_visible_intent(intents: list[Any]) -> bool:
    return any(not _is_typing_intent(intent) for intent in intents)


def _metadata_suppresses_typing(metadata: dict[str, Any]) -> bool:
    value = metadata.get("suppress_typing_indicator")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _with_final_typing_preface(
    intents: list[Any],
    *,
    channel: str,
    delivery_target: str,
    message_id: str,
    metadata: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    if not intents or not _has_visible_intent(intents) or _metadata_suppresses_typing(metadata):
        return intents, metadata

    preface_metadata = {
        **metadata,
        "inbound_message_id": metadata.get("inbound_message_id") or message_id,
        "typing_policy": _FINAL_PREFACE_TYPING_POLICY,
        "typing_dedupe_key": _final_preface_typing_dedupe_key(
            channel=channel,
            delivery_target=delivery_target,
            message_id=message_id,
        ),
    }
    prefaced_intents = intents if any(_is_typing_intent(intent) for intent in intents) else [SendTyping(), *intents]
    return prefaced_intents, preface_metadata


async def _send_delayed_initial_typing(
    *,
    publisher: QueuePublisher,
    channel: str,
    delivery_target: str,
    message_id: str,
    delay_seconds: float | None = None,
) -> None:
    start_time = time.perf_counter()
    try:
        resolved_delay_seconds = _INITIAL_TYPING_DELAY_SECONDS if delay_seconds is None else delay_seconds
        if resolved_delay_seconds > 0:
            await asyncio.sleep(resolved_delay_seconds)
        await enqueue_outbox_intents(
            publisher,
            delivery_target,
            channel,
            [SendTyping()],
            metadata={
                "source": "message_consumer",
                "message_id": message_id,
                "inbound_message_id": message_id,
                "dedupe_key": _initial_typing_dedupe_key(
                    channel=channel,
                    delivery_target=delivery_target,
                    message_id=message_id,
                ),
                "typing_policy": _INITIAL_TYPING_POLICY,
            },
        )
        logger.info(
            "message_consumer_initial_typing_enqueued",
            channel=channel,
            channel_user_id=delivery_target,
            message_id_hash=log_fingerprint(message_id),
            typing_policy=_INITIAL_TYPING_POLICY,
        )
        logger.info(
            "perf_timer_latency",
            gate="message_consumer_initial_typing_enqueue",
            duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            channel_user_id=delivery_target,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "message_consumer_initial_typing_failed",
            channel=channel,
            channel_user_id=delivery_target,
            message_id_hash=log_fingerprint(message_id),
            error_type=type(exc).__name__,
        )


class TypingHeartbeatController:
    """Bounded per-turn typing refresh loop for channels with expiring indicators."""

    def __init__(
        self,
        *,
        publisher: QueuePublisher,
        channel: str,
        delivery_target: str,
        message_id: str,
        enabled: bool | None = None,
        interval_seconds: float | None = None,
        max_seconds: float | None = None,
    ) -> None:
        self.publisher = publisher
        self.channel = channel
        self.delivery_target = delivery_target
        self.message_id = message_id
        self.enabled = settings.chat_typing_heartbeat_enabled if enabled is None else enabled
        self.interval_seconds = max(
            0.1,
            settings.chat_typing_heartbeat_interval_seconds
            if interval_seconds is None
            else float(interval_seconds),
        )
        self.max_seconds = max(
            0.0,
            settings.chat_typing_heartbeat_max_seconds if max_seconds is None else float(max_seconds),
        )
        self._task: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._sent_count = 0
        self._stop_reason = "not_started"
        self._summary_logged = False

    def start(self) -> None:
        if not self.enabled or self.max_seconds <= 0 or self._task is not None:
            return
        self._started_at = time.perf_counter()
        self._stop_reason = "running"
        self._task = asyncio.create_task(self._run(), name="message_consumer_typing_heartbeat")

    async def stop(self, reason: str) -> None:
        if self._task is None:
            return
        if not self._task.done():
            self._stop_reason = reason
            self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._log_summary(reason if self._stop_reason == "running" else self._stop_reason)
        self._task = None

    async def _run(self) -> None:
        assert self._started_at is not None
        sequence = 0
        try:
            while (time.perf_counter() - self._started_at) < self.max_seconds:
                await self._send_tick(sequence)
                sequence += 1
                remaining_seconds = self.max_seconds - (time.perf_counter() - self._started_at)
                if remaining_seconds <= 0:
                    break
                await asyncio.sleep(min(self.interval_seconds, remaining_seconds))
            if self._stop_reason == "running":
                self._stop_reason = "max_duration"
        except asyncio.CancelledError:
            raise

    async def _send_tick(self, sequence: int) -> None:
        try:
            await enqueue_outbox_intents(
                self.publisher,
                self.delivery_target,
                self.channel,
                [SendTyping()],
                metadata={
                    "source": "message_consumer",
                    "message_id": self.message_id,
                    "inbound_message_id": self.message_id,
                    "dedupe_key": _heartbeat_typing_dedupe_key(
                        channel=self.channel,
                        delivery_target=self.delivery_target,
                        message_id=self.message_id,
                        sequence=sequence,
                    ),
                    "typing_policy": _HEARTBEAT_TYPING_POLICY,
                    "typing_sequence": sequence,
                },
            )
            self._sent_count += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "typing_heartbeat_tick_failed",
                channel=self.channel,
                channel_user_id=self.delivery_target,
                message_id_hash=log_fingerprint(self.message_id),
                sequence=sequence,
                error_type=type(exc).__name__,
            )

    def _log_summary(self, stopped_reason: str) -> None:
        if self._summary_logged or self._started_at is None:
            return
        self._summary_logged = True
        logger.info(
            "typing_heartbeat_summary",
            channel=self.channel,
            channel_user_id=self.delivery_target,
            message_id_hash=log_fingerprint(self.message_id),
            sent_count=self._sent_count,
            duration_ms=round((time.perf_counter() - self._started_at) * 1000, 2),
            stopped_reason=stopped_reason,
        )


def _message_queue_age_ms(message: ChannelMessage) -> float | None:
    timestamp = message.timestamp
    if not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    age_ms = (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds() * 1000
    return max(0.0, age_ms)


async def _cancel_initial_typing_task(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return


class MessageConsumer:
    """Unified chat consumer used by the core ECS worker."""

    def __init__(
        self,
        user_repository: UserRepository | None,
        onboarding_executor: OnboardingExecutor | None,
        orchestrator: OrchestratorAgent | None,
        publisher: QueuePublisher | None = None,
        runtime_bundle_factory: ChatRuntimeBundleFactory | None = None,
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

    def _runtime_bundle(self) -> ChatRuntimeBundle:
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
        queue_age_ms = _message_queue_age_ms(message)
        if queue_age_ms is not None:
            self._log_latency_span(
                span="message_consumer_queue_age",
                duration_ms=queue_age_ms,
                channel_user_id=channel_user_id,
            )
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
        typing_heartbeat: TypingHeartbeatController | None = None
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
            typing_heartbeat = TypingHeartbeatController(
                publisher=self.publisher,
                channel=message.channel,
                delivery_target=channel_user_id,
                message_id=str(message.message_id),
            )
            typing_heartbeat.start()

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
                await typing_heartbeat.stop("receipt_choice")
                typing_heartbeat = None
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
                await typing_heartbeat.stop("safe_fallback")
                typing_heartbeat = None
                fallback_metadata = {
                    "source": "message_consumer",
                    "message_id": message.message_id,
                    "safe_fallback": True,
                }
                fallback_intents, fallback_metadata = _with_final_typing_preface(
                    [Say(text=response_text)],
                    channel=message.channel,
                    delivery_target=channel_user_id,
                    message_id=str(message.message_id),
                    metadata=fallback_metadata,
                )
                await enqueue_outbox_intents(
                    self.publisher,
                    channel_user_id,
                    message.channel,
                    fallback_intents,
                    metadata=fallback_metadata,
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
                intents_to_send, outbound_metadata = _with_final_typing_preface(
                    intents_to_send,
                    channel=message.channel,
                    delivery_target=channel_user_id,
                    message_id=str(message.message_id),
                    metadata=outbound_metadata,
                )
                outbox_start = time.perf_counter()
                await typing_heartbeat.stop("final_outbox_enqueue")
                typing_heartbeat = None
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
            if typing_heartbeat is not None:
                await typing_heartbeat.stop("no_visible_outbox")
                typing_heartbeat = None
        except Exception:
            if typing_heartbeat is not None:
                await typing_heartbeat.stop("exception")
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
