"""Message consumer dedupe tests."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.core.src.agent.orchestrator.models.intents import Say, ShowFlow
from apps.core.src.queue_consumers.message_consumer import MessageConsumer
from shared.cache.rate_limiter import RateLimitResult
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import ChannelMessage, MessageType


class _RateLimiterAllow:
    async def check(self, identifier: str) -> RateLimitResult:
        del identifier
        return RateLimitResult(allowed=True, remaining=9, reset_in_seconds=60, total_limit=10)


class _UserRepoStub:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db or SimpleNamespace(rollback=asyncio.sleep, commit=asyncio.sleep, close=asyncio.sleep)

    async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
        del channel, identity
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
        self.resume_calls: list[dict[str, str]] = []

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
        channel_identity: str | None = None,
    ) -> dict[str, Any]:
        del phone_number, text, message_id, message_type, media_id, quoted_message_id, channel, channel_identity
        self.invoke_calls += 1
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
        user_repository=_UserRepoStub(),
        onboarding_executor=_OnboardingStub(),
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())

    with pytest.raises(RuntimeError, match="invoke failed"):
        await consumer._handle_message(_message("wamid-fail"))

    assert context_manager.released == [("2348162511023", "wamid-fail")]


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

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    sent_payloads: list[list[Any]] = []

    async def _enqueue_outbox_intents(*args: Any, **kwargs: Any) -> None:
        del kwargs
        sent_payloads.append(list(args))

    monkeypatch.setattr(
        "apps.core.src.queue_consumers.message_consumer.enqueue_outbox_intents",
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
        "apps.core.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer._handle_pin_verified(
        flow_type="link",
        phone_number="2348162511023",
        success=True,
        channel="telegram",
        extra_data={"chat_id": "98765"},
    )

    assert orchestrator.resume_calls == []
    assert enqueue_calls == []


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

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.core.src.queue_consumers.message_consumer.enqueue_outbox_intents",
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

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.core.src.queue_consumers.message_consumer.enqueue_outbox_intents",
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

    monkeypatch.setattr("apps.core.src.queue_consumers.message_consumer.message_rate_limiter", _RateLimiterAllow())
    monkeypatch.setattr(
        "apps.core.src.queue_consumers.message_consumer.enqueue_outbox_intents",
        _enqueue_outbox_intents,
    )

    await consumer.process_message(_message("wamid-no-commit").model_dump(mode="json"))

    assert orchestrator.invoke_calls == 1
    assert session.rollback_calls == 0
    assert session.commit_calls == 0
    assert session.close_calls == 0
    assert len(enqueue_calls) == 1
