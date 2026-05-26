"""Unified core consumer for chat messages and flow events."""

import secrets
import time
from collections.abc import Callable
from typing import Any, cast

from apps.chat.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.chat.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowOptions,
    ShowReceipt,
    UiIntent,
)
from apps.chat.src.agent.orchestrator.presentation.intents import map_outbox_to_intents
from apps.chat.src.messaging.outbox import enqueue_outbox_intents, enqueue_outbox_say
from shared.cache.channel_identity_cache import load_channel_identity_user, store_channel_identity_user
from shared.cache.distributed_lock import RedisLockTimeoutError
from shared.cache.rate_limiter import message_rate_limiter
from shared.clients.telegram.client import TelegramClient
from shared.config.settings import settings
from shared.database.models import UserOnboardingStatusEnum
from shared.i18n import LocaleCode, render_message
from shared.i18n.locale import LocaleManager
from shared.messaging.prompt_suppression import (
    PENDING_INPUT_PROMPT_KIND,
    latest_inbound_delivery_target_key,
    pending_input_prompt_metadata,
)
from shared.models.messages import ChannelMessage
from shared.queue.adapter import QueuePublisher
from shared.queue.messages import FlowEventType
from shared.receipts.choice import (
    extract_receipt_choice_job,
    parse_receipt_choice_action,
    receipt_choice_claim_key,
)
from shared.repositories.user_repository import UserRepository
from shared.services.auth import AuthorizationService
from shared.services.channel_linking import build_channel_link_pin_token
from shared.services.onboarding import session_manager
from shared.utils.logging import get_logger, log_fingerprint
from shared.utils.sanitize import is_suspicious_input, sanitize_message

logger = get_logger(__name__)
_TRANSACTION_PIN_FLOWS = {"transfer", "airtime", "data", "schedule"}
_TELEGRAM_CHANNEL = "telegram"
_WHATSAPP_CHANNEL = "whatsapp"
_SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY = "_suppress_intermediate_input_prompt"
RuntimeBundleFactory = Callable[
    [],
    tuple[UserRepository, OnboardingExecutor, OrchestratorAgent],
]


class _NoopPublisher:
    async def publish(self, topic: Any, message: dict[str, Any]) -> None:
        del topic, message


def _new_channel_link_token() -> str:
    return f"channel-link-{secrets.token_urlsafe(32)}"


def _intent_kind(intent: UiIntent | dict[str, Any]) -> str:
    if isinstance(intent, dict):
        return str(intent.get("type") or "unknown")
    if isinstance(intent, Say):
        return "say"
    if isinstance(intent, RequestAuth):
        return "auth_request"
    if isinstance(intent, RequestConfirmation):
        return "request_confirmation"
    if isinstance(intent, ShowReceipt):
        return "show_receipt"
    if isinstance(intent, ShowFlow):
        return "flow"
    if isinstance(intent, ShowOptions):
        return "show_options"
    return type(intent).__name__.lower()


def _intent_text(intent: UiIntent | dict[str, Any]) -> str | None:
    if isinstance(intent, Say):
        return intent.text
    if isinstance(intent, dict) and intent.get("type") == "say":
        text = intent.get("text")
        return text if isinstance(text, str) else None
    return None


def _is_default_greeting_text(text: str | None) -> bool:
    if not text:
        return False
    normalized = " ".join(text.split())
    for locale in LocaleCode:
        greeting = str(render_message("conversational.greeting", locale.value))
        if normalized == " ".join(greeting.split()):
            return True
    return False


def _suppress_spurious_greeting_intents(intents: list[UiIntent | dict[str, Any]]) -> list[UiIntent | dict[str, Any]]:
    """Drop default greeting only when a substantive response is already queued."""
    has_non_greeting_visible_response = any(
        (text is not None and not _is_default_greeting_text(text))
        or isinstance(intent, (RequestAuth, RequestConfirmation, ShowReceipt, ShowFlow, ShowOptions))
        for intent in intents
        for text in [_intent_text(intent)]
    )
    if not has_non_greeting_visible_response:
        return intents

    filtered = [intent for intent in intents if not _is_default_greeting_text(_intent_text(intent))]
    if len(filtered) != len(intents):
        logger.warning(
            "message_consumer_spurious_greeting_suppressed",
            original_count=len(intents),
            filtered_count=len(filtered),
        )
    return filtered


def _is_pending_input_prompt_outbox(raw_outbox: Any) -> bool:
    if not isinstance(raw_outbox, list) or not raw_outbox:
        return False
    items = [item for item in raw_outbox if isinstance(item, dict)]
    return len(items) == len(raw_outbox) and all(
        item.get("prompt_kind") == PENDING_INPUT_PROMPT_KIND for item in items
    )


def _should_suppress_intermediate_input_prompt(message: ChannelMessage, raw_outbox: Any) -> bool:
    metadata = message.channel_metadata if isinstance(message.channel_metadata, dict) else {}
    return bool(metadata.get(_SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY)) and _is_pending_input_prompt_outbox(
        raw_outbox
    )


def _log_prepared_outbound(
    *,
    message: ChannelMessage,
    phone_number: str,
    response_text: str | None,
    intents: list[UiIntent | dict[str, Any]],
) -> None:
    text_hashes = [log_fingerprint(text) for intent in intents if (text := _intent_text(intent))]
    text_lengths = [len(text) for intent in intents if (text := _intent_text(intent))]
    logger.info(
        "message_consumer_outbound_prepared",
        message_id=message.message_id,
        channel=message.channel,
        phone_number=phone_number,
        intent_count=len(intents),
        intent_kinds=[_intent_kind(intent) for intent in intents],
        text_hashes=text_hashes,
        text_lengths=text_lengths,
        default_greeting_count=sum(1 for intent in intents if _is_default_greeting_text(_intent_text(intent))),
        response_text_hash=log_fingerprint(response_text),
        response_text_is_default_greeting=_is_default_greeting_text(response_text),
    )


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
    def _looks_like_phone_number(value: str) -> bool:
        normalized = value.strip()
        return normalized.isdigit() and 10 <= len(normalized) <= 15

    async def _resolve_channel_user(
        self,
        *,
        user_repository: UserRepository,
        channel: str,
        channel_user_id: str,
    ) -> Any | None:
        if channel == "whatsapp" and self._looks_like_phone_number(channel_user_id):
            return await user_repository.get_by_phone(channel_user_id)

        cached_user = await load_channel_identity_user(channel, channel_user_id)
        if cached_user is not None:
            return cached_user

        user = await user_repository.get_by_channel_identity(channel, channel_user_id)
        if user is not None:
            await store_channel_identity_user(channel, channel_user_id, user)
        return user

    def _resolve_latest_inbound_redis_client(self, orchestrator: OrchestratorAgent | None) -> Any | None:
        """Use the chat runtime Redis client when available; tests can omit it."""
        if self.latest_inbound_redis_client is not None:
            return self.latest_inbound_redis_client
        if orchestrator is None:
            return None

        deps = getattr(orchestrator, "deps", None)
        redis_client = getattr(deps, "redis_client", None)
        if redis_client is not None:
            return redis_client

        handler = getattr(orchestrator, "orchestrator_handler", None)
        return getattr(handler, "redis_client", None)

    def _resolve_actionable_message_repo(self, orchestrator: OrchestratorAgent | None) -> Any | None:
        if orchestrator is None:
            return None

        deps = getattr(orchestrator, "deps", None)
        repo = getattr(deps, "actionable_message_repo", None)
        if repo is not None:
            return repo

        handler = getattr(orchestrator, "orchestrator_handler", None)
        return getattr(handler, "actionable_message_repo", None)

    async def _remove_telegram_inline_keyboard(
        self,
        *,
        message: ChannelMessage,
        channel_user_id: str,
        clicked_message_id: str,
    ) -> None:
        if message.channel != _TELEGRAM_CHANNEL or not clicked_message_id:
            return

        try:
            await self.telegram_client_factory().remove_inline_keyboard(channel_user_id, clicked_message_id)
        except Exception as exc:
            logger.warning(
                "receipt_choice_telegram_keyboard_remove_failed",
                channel=message.channel,
                channel_user_id_hash=log_fingerprint(channel_user_id),
                clicked_message_id_hash=log_fingerprint(clicked_message_id),
                error_type=type(exc).__name__,
            )

    async def _release_receipt_choice_claim(
        self,
        *,
        redis_client: Any | None,
        channel: str,
        clicked_message_id: str,
    ) -> None:
        if redis_client is None:
            return
        try:
            delete = getattr(redis_client, "delete", None)
            if delete is not None:
                await delete(receipt_choice_claim_key(channel, clicked_message_id))
        except Exception as exc:
            logger.warning(
                "receipt_choice_claim_release_failed",
                channel=channel,
                clicked_message_id_hash=log_fingerprint(clicked_message_id),
                error=str(exc),
            )

    async def _record_latest_inbound_for_delivery_target(
        self,
        *,
        orchestrator: OrchestratorAgent | None,
        channel: str,
        delivery_target: str,
        message_id: str,
    ) -> None:
        redis_client = self._resolve_latest_inbound_redis_client(orchestrator)
        if redis_client is None:
            return

        key = latest_inbound_delivery_target_key(channel, delivery_target)
        try:
            await redis_client.set(
                key,
                str(message_id),
                ex=max(1, settings.chat_latest_inbound_ttl_seconds),
            )
        except Exception as exc:
            logger.warning(
                "message_consumer_latest_inbound_record_failed",
                channel=channel,
                channel_user_id=delivery_target,
                message_id_hash=log_fingerprint(message_id),
                error=str(exc),
            )

    async def _handle_receipt_image_choice(
        self,
        *,
        message: ChannelMessage,
        orchestrator: OrchestratorAgent | None,
        user: Any,
        phone_number: str,
        channel_user_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        if not parse_receipt_choice_action(text):
            return None

        locale = (await LocaleManager.get_effective_locale(phone_number)).value
        clicked_message_id = str(message.quoted_message_id or message.message_id or "").strip()
        actionable_repo = self._resolve_actionable_message_repo(orchestrator)

        if not clicked_message_id or actionable_repo is None:
            await enqueue_outbox_intents(
                self.publisher,
                channel_user_id,
                message.channel,
                [Say(text=render_message("query.receipt.failed", locale))],
                metadata={"source": "receipt_choice", "message_id": message.message_id},
            )
            return {"status": "receipt_image_failed", "reason": "actionable_unavailable"}

        actionable = await actionable_repo.get_by_channel_message_id_for_user(
            clicked_message_id,
            str(getattr(user, "id", "")),
        )
        receipt_job = extract_receipt_choice_job(getattr(actionable, "message_data", None))
        if receipt_job is None:
            await self._remove_telegram_inline_keyboard(
                message=message,
                channel_user_id=channel_user_id,
                clicked_message_id=clicked_message_id,
            )
            await enqueue_outbox_intents(
                self.publisher,
                channel_user_id,
                message.channel,
                [Say(text=render_message("query.receipt.expired", locale))],
                metadata={"source": "receipt_choice", "message_id": message.message_id},
            )
            return {"status": "receipt_image_expired"}

        redis_client = self._resolve_latest_inbound_redis_client(orchestrator)
        if redis_client is not None:
            try:
                claimed = await redis_client.set(
                    receipt_choice_claim_key(message.channel, clicked_message_id),
                    "1",
                    nx=True,
                )
            except Exception as exc:
                logger.warning(
                    "receipt_choice_claim_failed",
                    channel=message.channel,
                    clicked_message_id_hash=log_fingerprint(clicked_message_id),
                    error=str(exc),
                )
            else:
                if not claimed:
                    await self._remove_telegram_inline_keyboard(
                        message=message,
                        channel_user_id=channel_user_id,
                        clicked_message_id=clicked_message_id,
                    )
                    await enqueue_outbox_intents(
                        self.publisher,
                        channel_user_id,
                        message.channel,
                        [Say(text=render_message("query.receipt.already_generating", locale))],
                        metadata={"source": "receipt_choice", "message_id": message.message_id},
                    )
                    return {"status": "receipt_image_already_generating"}

        receipt_job = dict(receipt_job)
        receipt_job["language"] = locale
        receipt_job["send_generation_notice"] = True

        try:
            await self.publisher.publish("receipt.process", receipt_job)
        except Exception:
            logger.error(
                "receipt_choice_publish_failed",
                channel=message.channel,
                channel_user_id=channel_user_id,
                message_id=message.message_id,
                exc_info=True,
            )
            await self._release_receipt_choice_claim(
                redis_client=redis_client,
                channel=message.channel,
                clicked_message_id=clicked_message_id,
            )
            await enqueue_outbox_intents(
                self.publisher,
                channel_user_id,
                message.channel,
                [Say(text=render_message("query.receipt.failed", locale))],
                metadata={"source": "receipt_choice", "message_id": message.message_id},
            )
            return {"status": "receipt_image_failed", "reason": "publish_failed"}

        await self._remove_telegram_inline_keyboard(
            message=message,
            channel_user_id=channel_user_id,
            clicked_message_id=clicked_message_id,
        )
        return {"status": "receipt_image_accepted"}

    async def _gate_unlinked_whatsapp_identity(
        self,
        *,
        message: ChannelMessage,
        user: Any,
        user_repository: UserRepository,
    ) -> dict[str, Any] | None:
        """Require the existing Telegram channel to approve first-time WhatsApp access."""
        channel_user_id = message.channel_user_id
        if message.channel != _WHATSAPP_CHANNEL or not self._looks_like_phone_number(channel_user_id):
            return None

        if not hasattr(user_repository, "get_channel_identity_by_phone"):
            return None

        phone_number = str(getattr(user, "phone_number", "") or "").strip()
        authorizing_identity = await user_repository.get_channel_identity_by_phone(phone_number, _TELEGRAM_CHANNEL)
        if not authorizing_identity:
            return None

        existing_whatsapp_user = await user_repository.get_by_channel_identity(_WHATSAPP_CHANNEL, channel_user_id)
        if existing_whatsapp_user:
            if str(getattr(existing_whatsapp_user, "id", "")) != str(getattr(user, "id", "")):
                logger.warning(
                    "channel_link_identity_conflict",
                    requested_channel=_WHATSAPP_CHANNEL,
                    phone_number=phone_number,
                )
                await enqueue_outbox_say(
                    self.publisher,
                    channel_user_id,
                    _WHATSAPP_CHANNEL,
                    "This WhatsApp number is linked to another profile. Please contact support.",
                    metadata={"source": "channel_link_guard", "reason": "identity_conflict"},
                )
                return {"status": "channel_link_identity_conflict"}
            return None

        flow_token = _new_channel_link_token()
        stored = await session_manager.update_session_strict(
            flow_token,
            {
                "purpose": "channel_identity_link",
                "user_id": str(getattr(user, "id", "")),
                "phone_number": phone_number,
                "requested_channel": _WHATSAPP_CHANNEL,
                "requested_channel_user_id": channel_user_id,
                "requested_channel_actor_id": channel_user_id,
                "authorizing_channel": _TELEGRAM_CHANNEL,
                "authorizing_channel_user_id": str(authorizing_identity),
                "step": "pending_existing_channel_authorization",
            },
            verify=True,
        )
        if not stored:
            logger.error("channel_link_session_store_failed", requested_channel=_WHATSAPP_CHANNEL)
            await enqueue_outbox_say(
                self.publisher,
                channel_user_id,
                _WHATSAPP_CHANNEL,
                "I couldn't start that link request. Please try again.",
                metadata={"source": "channel_link_guard", "reason": "session_store_failed"},
            )
            return {"status": "channel_link_session_store_failed"}

        try:
            logger.info(
                "whatsapp_identity_link_telegram_pin_flow_send",
                flow_token_hash=log_fingerprint(flow_token),
                pin_flow_token_hash=log_fingerprint(build_channel_link_pin_token(flow_token)),
                authorizing_identity_hash=log_fingerprint(str(authorizing_identity)),
                requested_whatsapp_hash=log_fingerprint(channel_user_id),
            )
            result = await TelegramClient().send_flow(
                to=str(authorizing_identity),
                flow_id="pin_entry",
                flow_config={
                    "header": "Authorize WhatsApp link",
                    "text_body": (
                        "Enter your transaction PIN to link WhatsApp to your banking profile. "
                        "Continue only if this request was from you."
                    ),
                    "flow_cta": "Enter PIN",
                    "flow_token": build_channel_link_pin_token(flow_token),
                },
                suppress_typing_indicator=True,
            )
            logger.info(
                "whatsapp_identity_link_telegram_pin_flow_sent",
                flow_token_hash=log_fingerprint(flow_token),
                message_id=getattr(result, "message_id", None),
                requested_whatsapp_hash=log_fingerprint(channel_user_id),
            )
        except Exception as e:
            logger.error("channel_link_authorization_send_failed", requested_channel=_WHATSAPP_CHANNEL, error=str(e))
            await session_manager.delete_session(flow_token)
            await enqueue_outbox_say(
                self.publisher,
                channel_user_id,
                _WHATSAPP_CHANNEL,
                "I couldn't send the Telegram approval request. Please try again.",
                metadata={"source": "channel_link_guard", "reason": "authorization_send_failed"},
            )
            return {"status": "channel_link_authorization_send_failed"}

        if not result.success:
            logger.error(
                "channel_link_authorization_send_failed",
                requested_channel=_WHATSAPP_CHANNEL,
                error=result.error,
            )
            await session_manager.delete_session(flow_token)
            await enqueue_outbox_say(
                self.publisher,
                channel_user_id,
                _WHATSAPP_CHANNEL,
                "I couldn't send the Telegram approval request. Please try again.",
                metadata={"source": "channel_link_guard", "reason": "authorization_send_failed"},
            )
            return {"status": "channel_link_authorization_send_failed"}

        await enqueue_outbox_say(
            self.publisher,
            channel_user_id,
            _WHATSAPP_CHANNEL,
            (
                "I sent a secure PIN request to your existing Telegram channel. "
                "Enter your PIN there to finish linking WhatsApp."
            ),
            metadata={"source": "channel_link_guard", "reason": "authorization_pending"},
        )
        logger.info(
            "channel_link_authorization_requested",
            requested_channel=_WHATSAPP_CHANNEL,
            authorizing_channel=_TELEGRAM_CHANNEL,
        )
        return {
            "status": "channel_link_authorization_pending",
            "authorizing_channel": _TELEGRAM_CHANNEL,
        }

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
                await self._handle_pin_verified(
                    flow_type=flow_type,
                    phone_number=phone_number,
                    idempotency_key=idempotency_key,
                    success=success,
                    channel=channel,
                    extra_data=extra_data,
                    user_repository=user_repository,
                    orchestrator=orchestrator,
                )
            elif event_type == FlowEventType.PIN_FAILED.value:
                logger.info("pin_verification_failed", phone_hash=log_fingerprint(phone_number), flow_type=flow_type)
            else:
                logger.warning("unknown_flow_event", event_type=event_type, event_keys=sorted(event_data.keys()))
        except Exception as exc:
            logger.error(
                "flow_event_processing_failed",
                error_type=type(exc).__name__,
                event_keys=sorted(event_data.keys()),
                exc_info=True,
            )
            raise

    async def _handle_pin_verified(
        self,
        flow_type: str,
        phone_number: str,
        idempotency_key: str,
        success: Any,
        channel: str,
        extra_data: dict[str, Any] | None = None,
        user_repository: UserRepository | None = None,
        orchestrator: OrchestratorAgent | None = None,
    ) -> None:
        """Resume a paused transaction after a successful PIN flow."""
        runtime_orchestrator = orchestrator or self.orchestrator
        runtime_user_repository = user_repository or self.user_repository
        if success is not True:
            logger.warning(
                "pin_verified_but_not_success",
                phone_hash=log_fingerprint(phone_number),
                flow_type=flow_type,
            )
            return

        normalized_flow_type = flow_type.strip().lower()
        if normalized_flow_type not in _TRANSACTION_PIN_FLOWS:
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

        if runtime_user_repository is None:
            logger.error(
                "pin_verified_user_repository_missing",
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

        recorded_flow_type = str(auth_result.transaction_type or "").strip().lower()
        if recorded_flow_type != normalized_flow_type:
            logger.warning(
                "pin_verified_resume_flow_mismatch",
                phone_hash=log_fingerprint(phone_number),
                flow_type=flow_type,
                recorded_flow_type=recorded_flow_type,
            )
            return

        user = await runtime_user_repository.get_by_phone(phone_number)
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

        raw_outbox = [item for item in outbox if isinstance(item, dict)] if isinstance(outbox, list) else []
        intents_to_send = cast(list[UiIntent | dict[str, Any]], map_outbox_to_intents(raw_outbox, text))

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
        logger.info(
            "pin_response_enqueued_outbox",
            outbox_phone_hash=log_fingerprint(outbox_phone),
            mapped_from_hash=log_fingerprint(phone_number),
        )

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

        identity_start = time.perf_counter()
        user = await self._resolve_channel_user(
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

        link_gate_result = await self._gate_unlinked_whatsapp_identity(
            message=message,
            user=user,
            user_repository=runtime_user_repository,
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
            await self._record_latest_inbound_for_delivery_target(
                orchestrator=runtime_orchestrator,
                channel=message.channel,
                delivery_target=channel_user_id,
                message_id=str(message.message_id),
            )

            receipt_choice_result = await self._handle_receipt_image_choice(
                message=message,
                orchestrator=runtime_orchestrator,
                user=user,
                phone_number=phone_number,
                channel_user_id=channel_user_id,
                text=sanitized_text,
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

            intents: list[UiIntent] = orchestrator_output.get("intents", [])
            raw_outbox = orchestrator_output.get("outbox")
            response_text = orchestrator_output.get("text")
            delivery_metadata = (
                orchestrator_output.get("delivery_metadata")
                if isinstance(orchestrator_output.get("delivery_metadata"), dict)
                else {}
            )
            intents_to_send: list[UiIntent | dict[str, Any]]
            if intents:
                has_primary_interaction = any(
                    isinstance(intent, (RequestAuth, RequestConfirmation, ShowReceipt, ShowOptions, ShowFlow))
                    for intent in intents
                )
                if (
                    response_text
                    and not has_primary_interaction
                    and not any(isinstance(intent, Say) for intent in intents)
                ):
                    intents.append(Say(text=response_text))
                intents_to_send = cast(list[UiIntent | dict[str, Any]], intents)
            else:
                fallback_outbox = (
                    [item for item in raw_outbox if isinstance(item, dict)] if isinstance(raw_outbox, list) else []
                )
                intents_to_send = fallback_outbox
                if fallback_outbox:
                    logger.warning(
                        "message_consumer_empty_intents_falling_back_to_raw_outbox",
                        outbox_count=len(fallback_outbox),
                        message_id=message.message_id,
                    )

            if intents_to_send:
                if _should_suppress_intermediate_input_prompt(message, raw_outbox):
                    logger.info(
                        "message_consumer_intermediate_input_prompt_suppressed",
                        message_id=message.message_id,
                        channel=message.channel,
                        channel_user_id=channel_user_id,
                    )
                    intents_to_send = []
            if intents_to_send:
                intents_to_send = _suppress_spurious_greeting_intents(intents_to_send)
                _log_prepared_outbound(
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
                if _is_pending_input_prompt_outbox(raw_outbox):
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
