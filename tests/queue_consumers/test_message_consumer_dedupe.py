"""Message consumer dedupe tests."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.chat.src.queue_consumers import channel_link_gate as channel_link_gate_module
from apps.chat.src.queue_consumers import message_consumer as message_consumer_module
from apps.chat.src.queue_consumers import message_inbound as message_inbound_module
from apps.chat.src.queue_consumers import pin_resume as pin_resume_module
from apps.chat.src.queue_consumers.message_consumer import MessageConsumer
from banking.presentation.i18n.renderer import render_message
from banking.receipts.choice import (
    RECEIPT_IMAGE_ACTION_ID,
    build_receipt_choice_actionable_payload,
)
from banking.security.authorization import AuthorizationResult
from shared.cache.distributed_lock import RedisLockTimeoutError
from shared.cache.rate_limiter import RateLimitResult
from shared.database.models import UserOnboardingStatusEnum
from shared.messaging.intents import Say, ShowFlow
from shared.messaging.prompt_suppression import (
    PENDING_INPUT_PROMPT_METADATA_KEY,
    PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY,
    PENDING_INPUT_PROMPT_THREAD_KEY,
    latest_inbound_delivery_target_key,
)
from shared.models.messages import ChannelMessage, MessageType


class _RateLimiterAllow:
    async def check(self, identifier: str) -> RateLimitResult:
        del identifier
        return RateLimitResult(allowed=True, remaining=9, reset_in_seconds=60, total_limit=10)


class _UserRepoStub:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db or SimpleNamespace(rollback=asyncio.sleep, commit=asyncio.sleep, close=asyncio.sleep)
        self.channel_identity_calls = 0
        self.phone_calls = 0

    async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
        del channel, identity
        self.channel_identity_calls += 1
        return SimpleNamespace(
            id="u1",
            phone_number="2348162511023",
            onboarding_status=UserOnboardingStatusEnum.ONBOARDING_COMPLETED,
        )

    async def get_by_phone(self, phone_number: str) -> Any:
        del phone_number
        self.phone_calls += 1
        return SimpleNamespace(
            id="u1",
            phone_number="2348162511023",
            onboarding_status=UserOnboardingStatusEnum.ONBOARDING_COMPLETED,
        )


class _OnboardingStub:
    async def handle_onboarding(self, message: ChannelMessage) -> dict[str, Any]:
        del message
        return {"status": "onboarding"}


class _SessionStub:
    def __init__(self, *, pending_writes: bool = False, commit_error: Exception | None = None) -> None:
        self.rollback_calls = 0
        self.commit_calls = 0
        self.close_calls = 0
        self._commit_error = commit_error
        sync_session = SimpleNamespace(
            new=[object()] if pending_writes else [],
            dirty=[],
            deleted=[],
        )
        self.sync_session = sync_session

    async def rollback(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.rollback_calls += 1

    async def commit(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    async def close(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.close_calls += 1


class _ContextManagerStub:
    def __init__(self, should_claim: bool = True) -> None:
        self.should_claim = should_claim
        self.released: list[tuple[str, str]] = []
        self.saved: list[tuple[str, str]] = []
        self.claimed: list[tuple[str, str]] = []

    async def claim_inbound_message(self, phone_number: str, message_id: str, ttl_seconds: int = 86400) -> bool:
        del ttl_seconds
        self.claimed.append((phone_number, message_id))
        return self.should_claim

    async def release_inbound_message_claim(self, phone_number: str, message_id: str) -> None:
        self.released.append((phone_number, message_id))

    async def save_message_id(self, phone_number: str, message_id: str) -> None:
        self.saved.append((phone_number, message_id))


class _OrchestratorStub:
    def __init__(
        self,
        context_manager: _ContextManagerStub,
        *,
        should_fail: bool = False,
        output: dict[str, Any] | None = None,
        resume_output: dict[str, Any] | None = None,
    ) -> None:
        self.context_manager = context_manager
        self.should_fail = should_fail
        self.output = output
        self.resume_output = resume_output or {"text": "ok", "outbox": []}
        self.invoke_calls = 0
        self.last_user: Any | None = None
        self.last_mime_type: str | None = None
        self.last_channel_metadata: dict[str, Any] | None = None
        self.resume_calls: list[dict[str, str]] = []

    async def invoke(
        self,
        phone_number: str,
        text: str,
        message_id: str,
        *,
        message_type: str = "text",
        media_id: str | None = None,
        mime_type: str | None = None,
        quoted_message_id: str | None = None,
        channel: str = "whatsapp",
        channel_identity: str | None = None,
        channel_metadata: dict[str, Any] | None = None,
        user: Any | None = None,
    ) -> dict[str, Any]:
        del phone_number, text, message_id, message_type, media_id, quoted_message_id, channel, channel_identity
        self.last_channel_metadata = dict(channel_metadata or {})
        self.invoke_calls += 1
        self.last_user = user
        self.last_mime_type = mime_type
        if self.should_fail:
            raise RuntimeError("invoke failed")
        if self.output is not None:
            return self.output
        return {"intents": [Say(text="ok")], "text": "ok"}

    async def resume_transaction(
        self,
        phone_number: str,
        flow_type: str,
        pin_verified: bool,
        channel: str = "whatsapp",
    ) -> dict[str, Any]:
        self.resume_calls.append(
            {
                "phone_number": phone_number,
                "flow_type": flow_type,
                "pin_verified": str(pin_verified),
                "channel": channel,
            }
        )
        return self.resume_output


class _LockTimeoutOrchestratorStub(_OrchestratorStub):
    async def invoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        self.invoke_calls += 1
        raise RedisLockTimeoutError("redis_lock_acquire_timeout:chat:thread-lock:whatsapp:2348162511023")


def _message(message_id: str = "wamid-1") -> ChannelMessage:
    return ChannelMessage(
        message_id=message_id,
        channel_user_id="2348162511023",
        message_type=MessageType.TEXT,
        text="What's my balance",
        flow_data=None,
        media_id=None,
        mime_type=None,
        quoted_message_id=None,
        timestamp=datetime.now(UTC),
        channel="whatsapp",
    )


def _telegram_message(message_id: str = "tg-1") -> ChannelMessage:
    return ChannelMessage(
        message_id=message_id,
        channel_user_id="927331985",
        message_type=MessageType.TEXT,
        text="send 10k to mum",
        flow_data=None,
        media_id=None,
        mime_type=None,
        quoted_message_id=None,
        timestamp=datetime.now(UTC),
        channel="telegram",
    )


class _RedisStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.set_calls.append({"key": key, "value": value, "ex": ex})
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1 if self.values.pop(key, None) is not None else 0


class _SessionManagerStub:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    async def update_session_strict(
        self,
        flow_token: str,
        updates: dict[str, Any],
        *,
        verify: bool = False,
    ) -> bool:
        del verify
        self.sessions.setdefault(flow_token, {}).update(updates)
        return True

    async def delete_session(self, flow_token: str) -> None:
        self.deleted.append(flow_token)
        self.sessions.pop(flow_token, None)


class _AuthorizationServiceStub:
    def __init__(
        self,
        result: AuthorizationResult | None,
        *,
        claim_results: list[bool] | None = None,
    ) -> None:
        self.result = result
        self.claim_results = claim_results or [True]
        self.get_calls: list[str] = []
        self.claim_calls: list[tuple[str, int]] = []

    async def get_pin_verification_result(self, idempotency_key: str) -> AuthorizationResult | None:
        self.get_calls.append(idempotency_key)
        return self.result

    async def claim_pin_resume(self, idempotency_key: str, ttl_seconds: int = 86400) -> bool:
        self.claim_calls.append((idempotency_key, ttl_seconds))
        if not self.claim_results:
            return False
        return self.claim_results.pop(0)


def _pin_verified_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "event_type": "pin_verified",
        "phone_number": "2348162511023",
        "flow_type": "transfer",
        "idempotency_key": "idem-1",
        "success": True,
        "channel": "whatsapp",
    }
    event.update(overrides)
    return event


@pytest.mark.asyncio
async def test_duplicate_message_id_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    enqueue_calls: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        enqueue_calls.append(list(args))

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    first = await consumer._handle_message(_message("wamid-dup"))
    assert first is not None
    assert first["status"] == "success"
    assert orchestrator.invoke_calls == 1
    assert len(enqueue_calls) == 1

    context_manager.should_claim = False
    second = await consumer._handle_message(_message("wamid-dup"))
    assert second is not None
    assert second["status"] == "duplicate_ignored"
    assert orchestrator.invoke_calls == 1
    assert len(enqueue_calls) == 1


@pytest.mark.asyncio
async def test_message_consumer_passes_channel_metadata_to_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    message = _message("wamid-meta").model_copy(update={"channel_metadata": {"sender_display_name": "Gaines Abiodun"}})

    result = await consumer._handle_message(message)

    assert result is not None
    assert result["status"] == "success"
    assert orchestrator.last_channel_metadata == {"sender_display_name": "Gaines Abiodun"}


@pytest.mark.asyncio
async def test_message_consumer_passes_media_mime_type(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    message = _message("wamid-image")
    message.message_type = MessageType.IMAGE
    message.media_id = "media-1"
    message.mime_type = "image/png"

    response = await consumer._handle_message(message)

    assert response is not None
    assert response["status"] == "success"
    assert orchestrator.last_mime_type == "image/png"


@pytest.mark.asyncio
async def test_message_consumer_lock_timeout_releases_claim_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _LockTimeoutOrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    enqueue_outbox_intents = AsyncMock()

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    with pytest.raises(RedisLockTimeoutError):
        await consumer._handle_message(_message("wamid-lock-timeout"))

    assert context_manager.released == [("2348162511023", "wamid-lock-timeout")]
    enqueue_outbox_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_consumer_passes_resolved_user_to_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    user_repository = _UserRepoStub()
    consumer = MessageConsumer(
        user_repository=user_repository,
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer._handle_message(_message("wamid-user-pass"))

    assert orchestrator.last_user is not None
    assert getattr(orchestrator.last_user, "phone_number", None) == "2348162511023"
    assert user_repository.phone_calls == 1
    assert user_repository.channel_identity_calls == 0


@pytest.mark.asyncio
async def test_whatsapp_message_requires_telegram_approval_before_linking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id="u1",
        phone_number="2348162511023",
        onboarding_status=UserOnboardingStatusEnum.ONBOARDING_COMPLETED,
    )

    class _LinkingUserRepoStub:
        async def get_by_phone(self, phone_number: str) -> Any:
            assert phone_number == "2348162511023"
            return user

        async def get_channel_identity_by_phone(self, phone_number: str, channel: str) -> str | None:
            assert phone_number == "2348162511023"
            assert channel == "telegram"
            return "927331985"

        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "whatsapp"
            assert identity == "2348162511023"
            return None

    class _TelegramClientStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send_flow(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return SimpleNamespace(success=True, error=None, message_id="tg-flow")

    session_manager = _SessionManagerStub()
    telegram_client = _TelegramClientStub()
    say_calls: list[tuple[str, str, str, str, dict[str, Any] | None]] = []

    async def _enqueue_outbox_say(
        publisher: Any,
        phone_number: str,
        channel: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del publisher
        say_calls.append((phone_number, channel, text, metadata))

    monkeypatch.setattr(message_consumer_module, "message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(channel_link_gate_module, "session_manager", session_manager)
    monkeypatch.setattr(channel_link_gate_module.secrets, "token_urlsafe", lambda _: "opaque-token")
    monkeypatch.setattr(message_consumer_module, "TelegramClient", lambda: telegram_client)
    monkeypatch.setattr(channel_link_gate_module, "enqueue_outbox_say", _enqueue_outbox_say)

    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_LinkingUserRepoStub(),  # type: ignore[arg-type]
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    result = await consumer._handle_message(_message("wamid-link-whatsapp"))

    assert result == {"status": "channel_link_authorization_pending", "authorizing_channel": "telegram"}
    assert orchestrator.invoke_calls == 0
    assert context_manager.claimed == []
    assert session_manager.sessions["channel-link-opaque-token"] == {
        "purpose": "channel_identity_link",
        "user_id": "u1",
        "phone_number": "2348162511023",
        "requested_channel": "whatsapp",
        "requested_channel_user_id": "2348162511023",
        "requested_channel_actor_id": "2348162511023",
        "authorizing_channel": "telegram",
        "authorizing_channel_user_id": "927331985",
        "step": "pending_existing_channel_authorization",
    }
    assert telegram_client.calls == [
        {
            "to": "927331985",
            "flow_id": "pin_entry",
            "flow_config": {
                "header": "Authorize WhatsApp link",
                "text_body": (
                    "Enter your transaction PIN to link WhatsApp to your banking profile. "
                    "Continue only if this request was from you."
                ),
                "flow_cta": "Enter PIN",
                "flow_token": "channel-link-pin-channel-link-opaque-token",
            },
            "suppress_typing_indicator": True,
        }
    ]
    assert say_calls == [
        (
            "2348162511023",
            "whatsapp",
            (
                "I sent a secure PIN request to your existing Telegram channel. "
                "Enter your PIN there to finish linking WhatsApp."
            ),
            {"source": "channel_link_guard", "reason": "authorization_pending"},
        )
    ]


@pytest.mark.asyncio
async def test_message_consumer_uses_cached_telegram_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    user_repository = _UserRepoStub()
    consumer = MessageConsumer(
        user_repository=user_repository,
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    redis_stub = _RedisStub()
    redis_stub.values["cache:channel_identity:telegram:927331985"] = json.dumps(
        {
            "id": "dbfea933-7738-4f87-8a15-98ba39da189c",
            "phone_number": "2348162511023",
            "onboarding_status": UserOnboardingStatusEnum.ONBOARDING_COMPLETED.value,
            "full_name": "Olamide Samuel",
        }
    )

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr("shared.cache.channel_identity_cache.RedisClient.get_client", lambda: redis_stub)
    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer._handle_message(_telegram_message("tg-cache-hit"))

    assert orchestrator.last_user is not None
    assert getattr(orchestrator.last_user, "phone_number", None) == "2348162511023"
    assert user_repository.channel_identity_calls == 0
    assert user_repository.phone_calls == 0


@pytest.mark.asyncio
async def test_message_consumer_refetches_invalid_cached_telegram_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    user_repository = _UserRepoStub()
    consumer = MessageConsumer(
        user_repository=user_repository,
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    redis_stub = _RedisStub()
    redis_stub.values["cache:channel_identity:telegram:927331985"] = json.dumps(
        {
            "id": "u1",
            "phone_number": "2348162511023",
            "onboarding_status": UserOnboardingStatusEnum.ONBOARDING_COMPLETED.value,
            "full_name": "Olamide Samuel",
        }
    )

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr("shared.cache.channel_identity_cache.RedisClient.get_client", lambda: redis_stub)
    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer._handle_message(_telegram_message("tg-cache-invalid"))

    assert orchestrator.last_user is not None
    assert getattr(orchestrator.last_user, "phone_number", None) == "2348162511023"
    assert user_repository.channel_identity_calls == 1
    assert user_repository.phone_calls == 0
    assert redis_stub.deleted == ["cache:channel_identity:telegram:927331985"]


@pytest.mark.asyncio
async def test_safe_fallback_is_sent_when_orchestrator_invoke_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager, should_fail=True)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    sent_payloads: list[list[Any]] = []
    sent_kwargs: list[dict[str, Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        sent_payloads.append(list(args))
        sent_kwargs.append(kwargs)

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    response = await consumer._handle_message(_message("wamid-fail"))

    assert response is not None
    assert response["status"] == "safe_fallback"
    assert response["response"] == "I'm sorry, I'm having trouble processing that right now."
    assert context_manager.released == []
    assert len(sent_payloads) == 1
    intents = sent_payloads[0][3]
    assert len(intents) == 1
    assert isinstance(intents[0], Say)
    assert intents[0].text == "I'm sorry, I'm having trouble processing that right now."
    assert sent_kwargs == [
        {
            "metadata": {
                "source": "message_consumer",
                "message_id": "wamid-fail",
                "safe_fallback": True,
            }
        }
    ]


@pytest.mark.asyncio
async def test_message_consumer_does_not_append_say_for_show_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [
                ShowFlow(
                    flow_id="flow_123",
                    flow_config={"header": "Link New Account"},
                    fallback_text="Open flow",
                )
            ],
            "text": "This should not become an extra Say",
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    sent_payloads: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        sent_payloads.append(list(args))

    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    response = await consumer._handle_message(_message("wamid-flow-1"))

    assert response is not None
    assert response["status"] == "success"
    assert len(sent_payloads) == 1
    intents = sent_payloads[0][3]
    assert len(intents) == 1
    assert isinstance(intents[0], ShowFlow)


@pytest.mark.asyncio
async def test_message_consumer_suppresses_default_greeting_when_domain_answer_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    domain_answer = "*Transactions* — Apr 19-May 19\n\n*May 17*\nN10,000"
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [
                Say(text=domain_answer),
                Say(text=render_message("conversational.greeting", "en")),
            ],
            "text": render_message("conversational.greeting", "en"),
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    sent_payloads: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        sent_payloads.append(list(args))

    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    response = await consumer._handle_message(_message("wamid-domain-greeting"))

    assert response is not None
    assert response["status"] == "success"
    assert len(sent_payloads) == 1
    intents = sent_payloads[0][3]
    assert len(intents) == 1
    assert isinstance(intents[0], Say)
    assert intents[0].text == domain_answer


@pytest.mark.asyncio
async def test_message_consumer_falls_back_to_raw_outbox_when_intents_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [],
            "outbox": [{"type": "say", "text": "I am generating your receipt now."}],
            "text": None,
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    sent_payloads: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        sent_payloads.append(list(args))

    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    response = await consumer._handle_message(_message("wamid-outbox-fallback"))

    assert response is not None
    assert response["status"] == "success"
    assert len(sent_payloads) == 1
    assert sent_payloads[0][3] == [{"type": "say", "text": "I am generating your receipt now."}]


@pytest.mark.asyncio
async def test_message_consumer_suppresses_intermediate_input_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [Say(text="How much would you like to send?")],
            "outbox": [{"type": "say", "text": "How much would you like to send?", "prompt_kind": "pending_input"}],
            "text": None,
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    enqueue_outbox_intents = AsyncMock()
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    message = _message("wamid-intermediate-prompt")
    message.channel_metadata["_suppress_intermediate_input_prompt"] = True

    response = await consumer._handle_message(message)

    assert response is not None
    assert response["status"] == "success"
    enqueue_outbox_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_consumer_marks_pending_input_prompt_for_receipt_staleness_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [Say(text="How much would you like to send?")],
            "outbox": [{"type": "say", "text": "How much would you like to send?", "prompt_kind": "pending_input"}],
            "text": None,
        },
    )
    redis = _RedisStub()
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
        latest_inbound_redis_client=redis,
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    enqueue_outbox_intents = AsyncMock()
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    message = _message("wamid-pending-prompt")
    response = await consumer._handle_message(message)

    assert response is not None
    assert response["status"] == "success"
    key = latest_inbound_delivery_target_key("whatsapp", "2348162511023")
    assert redis.values[key] == "wamid-pending-prompt"
    assert redis.set_calls == [
        {
            "key": key,
            "value": "wamid-pending-prompt",
            "ex": message_inbound_module.settings.chat_latest_inbound_ttl_seconds,
        }
    ]
    metadata = enqueue_outbox_intents.await_args.kwargs["metadata"]
    assert metadata[PENDING_INPUT_PROMPT_METADATA_KEY] is True
    assert metadata[PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY] == "wamid-pending-prompt"
    assert metadata[PENDING_INPUT_PROMPT_THREAD_KEY] == key


@pytest.mark.asyncio
async def test_message_consumer_keeps_intermediate_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    confirmation = {
        "type": "request_confirmation",
        "task_ids": ["t1"],
        "summary": "Confirm Transfer\n₦3,000 → Tolu",
        "idempotency_key": "idem-1",
    }
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [],
            "outbox": [confirmation],
            "text": None,
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    enqueue_outbox_intents = AsyncMock()
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    message = _message("wamid-intermediate-confirmation")
    message.channel_metadata["_suppress_intermediate_input_prompt"] = True

    response = await consumer._handle_message(message)

    assert response is not None
    assert response["status"] == "success"
    enqueue_outbox_intents.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_consumer_accepts_receipt_image_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    redis = _RedisStub()
    publisher = SimpleNamespace(publish=AsyncMock())
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
        publisher=publisher,
        latest_inbound_redis_client=redis,
    )
    receipt_job = {"phone_number": "2348162511023", "transaction_reference": "tx-1"}
    repo = SimpleNamespace(
        get_by_channel_message_id_for_user=AsyncMock(
            return_value=SimpleNamespace(message_data=build_receipt_choice_actionable_payload(receipt_job))
        )
    )
    orchestrator.deps = SimpleNamespace(actionable_message_repo=repo)

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(message_inbound_module, "load_channel_identity_user", AsyncMock(return_value=None))
    monkeypatch.setattr(message_inbound_module, "store_channel_identity_user", AsyncMock())
    enqueue_outbox_intents = AsyncMock()
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.receipt_choices.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    message = _message("wamid-receipt-yes")
    message.text = RECEIPT_IMAGE_ACTION_ID
    message.quoted_message_id = "wamid-receipt-offer"
    response = await consumer._handle_message(message)

    assert response == {"status": "receipt_image_accepted"}
    assert orchestrator.invoke_calls == 0
    repo.get_by_channel_message_id_for_user.assert_awaited_once_with("wamid-receipt-offer", "u1")
    publisher.publish.assert_awaited_once_with(
        "receipt.process",
        {**receipt_job, "language": "en", "send_generation_notice": True},
    )
    enqueue_outbox_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_consumer_accepts_telegram_receipt_image_choice_removes_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    redis = _RedisStub()
    publisher = SimpleNamespace(publish=AsyncMock())
    telegram_client = SimpleNamespace(remove_inline_keyboard=AsyncMock(return_value=True))
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
        publisher=publisher,
        latest_inbound_redis_client=redis,
        telegram_client_factory=lambda: telegram_client,
    )
    receipt_job = {"phone_number": "927331985", "transaction_reference": "tx-telegram-1"}
    repo = SimpleNamespace(
        get_by_channel_message_id_for_user=AsyncMock(
            return_value=SimpleNamespace(message_data=build_receipt_choice_actionable_payload(receipt_job))
        )
    )
    orchestrator.deps = SimpleNamespace(actionable_message_repo=repo)

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(message_inbound_module, "load_channel_identity_user", AsyncMock(return_value=None))
    monkeypatch.setattr(message_inbound_module, "store_channel_identity_user", AsyncMock())
    enqueue_outbox_intents = AsyncMock()
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.receipt_choices.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    message = _telegram_message("tg-receipt-offer")
    message.text = RECEIPT_IMAGE_ACTION_ID
    response = await consumer._handle_message(message)

    assert response == {"status": "receipt_image_accepted"}
    assert orchestrator.invoke_calls == 0
    repo.get_by_channel_message_id_for_user.assert_awaited_once_with("tg-receipt-offer", "u1")
    publisher.publish.assert_awaited_once_with(
        "receipt.process",
        {**receipt_job, "language": "en", "send_generation_notice": True},
    )
    telegram_client.remove_inline_keyboard.assert_awaited_once_with("927331985", "tg-receipt-offer")
    enqueue_outbox_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_consumer_receipt_image_choice_missing_payload_expires_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    publisher = SimpleNamespace(publish=AsyncMock())
    repo = SimpleNamespace(get_by_channel_message_id_for_user=AsyncMock(return_value=None))
    orchestrator.deps = SimpleNamespace(actionable_message_repo=repo)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
        publisher=publisher,
        latest_inbound_redis_client=_RedisStub(),
    )

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    enqueue_outbox_intents = AsyncMock()
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.receipt_choices.enqueue_outbox_intents",
        enqueue_outbox_intents,
    )

    message = _message("wamid-receipt-missing")
    message.text = RECEIPT_IMAGE_ACTION_ID
    message.quoted_message_id = "wamid-receipt-offer-missing"
    response = await consumer._handle_message(message)

    assert response == {"status": "receipt_image_expired"}
    assert orchestrator.invoke_calls == 0
    publisher.publish.assert_not_awaited()
    enqueue_outbox_intents.assert_awaited_once()
    assert enqueue_outbox_intents.await_args.args[3][0].text == render_message("query.receipt.expired", "en")


@pytest.mark.asyncio
async def test_non_transaction_pin_verified_flow_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    enqueue_calls: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        enqueue_calls.append(list(args))

    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.pin_resume.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await pin_resume_module.handle_pin_verified(
        flow_type="link",
        phone_number="2348162511023",
        idempotency_key="idem-1",
        success=True,
        channel="telegram",
        publisher=consumer.publisher,
        extra_data={"chat_id": "98765"},
        user_repository=consumer.user_repository,
        orchestrator=consumer.orchestrator,
    )

    assert orchestrator.resume_calls == []
    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_pin_verified_event_without_stored_authorization_does_not_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    auth_service = _AuthorizationServiceStub(None)
    monkeypatch.setattr(pin_resume_module, "AuthorizationService", lambda: auth_service)

    await consumer.process_flow_event(_pin_verified_event())

    assert auth_service.get_calls == ["idem-1"]
    assert auth_service.claim_calls == []
    assert orchestrator.resume_calls == []


@pytest.mark.asyncio
async def test_pin_verified_event_requires_literal_success_true(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    auth_service = _AuthorizationServiceStub(
        AuthorizationResult(verified=True, user_id="u1", transaction_type="transfer")
    )
    monkeypatch.setattr(pin_resume_module, "AuthorizationService", lambda: auth_service)

    await consumer.process_flow_event(_pin_verified_event(success="true"))

    assert auth_service.get_calls == []
    assert auth_service.claim_calls == []
    assert orchestrator.resume_calls == []


@pytest.mark.parametrize(
    ("auth_result", "flow_type"),
    [
        (AuthorizationResult(verified=False, user_id="u1", transaction_type="transfer"), "transfer"),
        (AuthorizationResult(verified=True, user_id=None, transaction_type="transfer"), "transfer"),
        (AuthorizationResult(verified=True, user_id="u1", transaction_type="airtime"), "transfer"),
        (AuthorizationResult(verified=True, user_id="u2", transaction_type="transfer"), "transfer"),
    ],
)
@pytest.mark.asyncio
async def test_pin_verified_event_rejects_invalid_stored_authorization(
    monkeypatch: pytest.MonkeyPatch,
    auth_result: AuthorizationResult,
    flow_type: str,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    auth_service = _AuthorizationServiceStub(auth_result)
    monkeypatch.setattr(pin_resume_module, "AuthorizationService", lambda: auth_service)

    await consumer.process_flow_event(_pin_verified_event(flow_type=flow_type))

    assert auth_service.get_calls == ["idem-1"]
    assert auth_service.claim_calls == []
    assert orchestrator.resume_calls == []


@pytest.mark.asyncio
async def test_pin_verified_event_resumes_once_after_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    auth_service = _AuthorizationServiceStub(
        AuthorizationResult(verified=True, user_id="u1", transaction_type="transfer"),
        claim_results=[True, False],
    )
    sent_payloads: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        sent_payloads.append(list(args))

    monkeypatch.setattr(pin_resume_module, "AuthorizationService", lambda: auth_service)
    monkeypatch.setattr(pin_resume_module, "enqueue_outbox_intents", _enqueue_outbox_intents)

    await consumer.process_flow_event(_pin_verified_event())
    await consumer.process_flow_event(_pin_verified_event())

    assert auth_service.get_calls == ["idem-1", "idem-1"]
    assert auth_service.claim_calls == [("idem-1", 86400), ("idem-1", 86400)]
    assert orchestrator.resume_calls == [
        {
            "phone_number": "2348162511023",
            "flow_type": "transfer",
            "pin_verified": "True",
            "channel": "whatsapp",
        }
    ]
    assert len(sent_payloads) == 1


@pytest.mark.asyncio
async def test_pin_verified_event_accepts_schedule_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    auth_service = _AuthorizationServiceStub(
        AuthorizationResult(verified=True, user_id="u1", transaction_type="schedule"),
        claim_results=[True],
    )

    monkeypatch.setattr(pin_resume_module, "AuthorizationService", lambda: auth_service)

    await consumer.process_flow_event(_pin_verified_event(flow_type="schedule"))

    assert orchestrator.resume_calls == [
        {
            "phone_number": "2348162511023",
            "flow_type": "schedule",
            "pin_verified": "True",
            "channel": "whatsapp",
        }
    ]


@pytest.mark.asyncio
async def test_pin_verified_resume_does_not_duplicate_final_response_and_outbox_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    response_text = render_message("orchestrator.session.transaction_expired", "en")
    orchestrator = _OrchestratorStub(
        context_manager,
        resume_output={
            "text": response_text,
            "final_response": response_text,
            "outbox": [{"type": "say", "text": response_text}],
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )
    auth_service = _AuthorizationServiceStub(
        AuthorizationResult(verified=True, user_id="u1", transaction_type="transfer"),
        claim_results=[True],
    )
    sent_payloads: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        sent_payloads.append(list(args))

    monkeypatch.setattr(pin_resume_module, "AuthorizationService", lambda: auth_service)
    monkeypatch.setattr(pin_resume_module, "enqueue_outbox_intents", _enqueue_outbox_intents)

    await consumer.process_flow_event(_pin_verified_event())

    assert len(sent_payloads) == 1
    intents = sent_payloads[0][3]
    assert len(intents) == 1
    assert isinstance(intents[0], Say)
    assert intents[0].text == response_text


@pytest.mark.asyncio
async def test_message_consumer_passes_delivery_metadata_to_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(
        context_manager,
        output={
            "intents": [Say(text="done")],
            "text": "done",
            "delivery_metadata": {"suppress_typing_indicator": True},
        },
    )
    consumer = MessageConsumer(
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    captured_kwargs: list[dict[str, Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del args
        captured_kwargs.append(kwargs)

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    response = await consumer._handle_message(_message("wamid-meta-1"))

    assert response is not None
    assert response["status"] == "success"
    assert captured_kwargs == [
        {
            "metadata": {
                "source": "message_consumer",
                "message_id": "wamid-meta-1",
                "suppress_typing_indicator": True,
            }
        }
    ]


@pytest.mark.asyncio
async def test_process_message_uses_runtime_bundle_without_outer_db_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub()
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    user_repo = _UserRepoStub(db=session)

    consumer = MessageConsumer(
        user_repository=None,
        onboarding_executor=None,
        orchestrator=None,
        runtime_bundle_factory=lambda: (user_repo, _OnboardingStub(), orchestrator),
    )

    enqueue_calls: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        enqueue_calls.append(list(args))

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer.process_message(_message("wamid-fresh").model_dump(mode="json"))

    assert orchestrator.invoke_calls == 1
    assert session.rollback_calls == 0
    assert session.commit_calls == 0
    assert session.close_calls == 0
    assert len(enqueue_calls) == 1


@pytest.mark.asyncio
async def test_process_message_does_not_use_outer_db_session_even_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub(commit_error=RuntimeError("commit should be skipped"))
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    user_repo = _UserRepoStub(db=session)

    consumer = MessageConsumer(
        user_repository=None,
        onboarding_executor=None,
        orchestrator=None,
        runtime_bundle_factory=lambda: (user_repo, _OnboardingStub(), orchestrator),
    )

    enqueue_calls: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        enqueue_calls.append(list(args))

    monkeypatch.setattr("apps.chat.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.chat.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer.process_message(_message("wamid-no-commit").model_dump(mode="json"))

    assert orchestrator.invoke_calls == 1
    assert session.rollback_calls == 0
    assert session.commit_calls == 0
    assert session.close_calls == 0
    assert len(enqueue_calls) == 1
