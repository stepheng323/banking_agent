"""Message consumer dedupe tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.core.src.agent.orchestrator.models.intents import Say
from apps.core.src.queue_consumers.message_consumer import MessageConsumer
from shared.cache.rate_limiter import RateLimitResult
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import MessageType, WhatsAppMessage


class _RateLimiterAllow:
    async def check(self, identifier: str) -> RateLimitResult:
        del identifier
        return RateLimitResult(allowed=True, remaining=9, reset_in_seconds=60, total_limit=10)


class _QueueStub:
    async def enqueue(self, queue_name: str, message: dict[str, Any]) -> None:
        del queue_name, message


class _UserRepoStub:
    async def get_by_phone(self, phone_number: str) -> Any:
        del phone_number
        return SimpleNamespace(id="u1", onboarding_status=UserOnboardingStatusEnum.ONBOARDING_COMPLETED)


class _OnboardingStub:
    async def handle_onboarding(self, message: WhatsAppMessage) -> dict[str, Any]:
        del message
        return {"status": "onboarding"}


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
    def __init__(self, context_manager: _ContextManagerStub, *, should_fail: bool = False) -> None:
        self.context_manager = context_manager
        self.should_fail = should_fail
        self.invoke_calls = 0

    async def invoke(
        self,
        phone_number: str,
        text: str,
        message_id: str,
        *,
        message_type: str = "text",
        media_id: str | None = None,
        quoted_message_id: str | None = None,
        channel: str = "whatsapp",
    ) -> dict[str, Any]:
        del phone_number, text, message_id, message_type, media_id, quoted_message_id, channel
        self.invoke_calls += 1
        if self.should_fail:
            raise RuntimeError("invoke failed")
        return {"intents": [Say(text="ok")], "text": "ok"}


def _message(message_id: str = "wamid-1") -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=message_id,
        from_number="2348162511023",
        message_type=MessageType.TEXT,
        text="What's my balance",
        flow_data=None,
        media_id=None,
        mime_type=None,
        quoted_message_id=None,
        timestamp=datetime.now(UTC),
        channel="whatsapp",
    )


@pytest.mark.asyncio
async def test_duplicate_message_id_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager)
    consumer = MessageConsumer(
        redis_queue=_QueueStub(),
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    enqueue_calls: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        enqueue_calls.append(list(args))

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.core.src.queue_consumers.message_consumer.enqueue_outbox_intents",
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
async def test_claim_is_released_when_processing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    context_manager = _ContextManagerStub(should_claim=True)
    orchestrator = _OrchestratorStub(context_manager, should_fail=True)
    consumer = MessageConsumer(
        redis_queue=_QueueStub(),
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())

    with pytest.raises(RuntimeError, match="invoke failed"):
        await consumer._handle_message(_message("wamid-fail"))

    assert context_manager.released == [("2348162511023", "wamid-fail")]
