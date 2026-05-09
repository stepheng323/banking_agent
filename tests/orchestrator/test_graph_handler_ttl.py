import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.graph.handler import OrchestratorGraphHandler
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext


class _CheckpointerStub:
    async def asetup(self) -> None:
        return

    async def adelete_thread(self, thread_id: str) -> None:
        del thread_id
        return


class _GraphStub:
    async def ainvoke(self, inputs: dict[str, object], config: dict[str, object]) -> dict[str, object]:
        del inputs, config
        return {"outbox": [], "final_response": None, "loaded_context": {"language": "en"}}


class _ConcurrentGraphStub:
    def __init__(self, *, delay: float = 0.05) -> None:
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0
        self.thread_ids: list[str] = []

    async def ainvoke(self, inputs: dict[str, object], config: dict[str, object]) -> dict[str, object]:
        del inputs
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")
            if isinstance(thread_id, str):
                self.thread_ids.append(thread_id)
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(self.delay)
            return {"outbox": [], "final_response": None, "loaded_context": {"language": "en"}}
        finally:
            self.active_calls -= 1


class _ContextManagerStub:
    async def load_context_parallel(
        self,
        phone_number: str,
        *,
        path_label: str = "planner_path",
        user: object | None = None,
        profile_mode: str = "full",
        account_mode: str = "full",
        beneficiary_mode: str = "full",
    ) -> tuple[dict[str, object], None, None, None]:
        del path_label, user, profile_mode, account_mode, beneficiary_mode
        del phone_number
        return (
            {
                "profile": {"id": str(uuid4())},
                "accounts": [],
                "beneficiaries": [],
                "history": [],
                "language": "en",
            },
            None,
            None,
            None,
        )


class _PipelineStub:
    def __init__(self) -> None:
        self.expire_calls: list[tuple[str, int]] = []

    def expire(self, key: str, ttl: int) -> None:
        self.expire_calls.append((key, ttl))

    async def execute(self) -> list[bool]:
        return [True] * len(self.expire_calls)


class _RedisStub:
    def __init__(self, matches: dict[str, list[str]] | None = None) -> None:
        self.matches = matches or {}
        self.expire_calls: list[tuple[str, int]] = []
        self.pipeline_instances: list[_PipelineStub] = []

    async def scan_iter(self, match: str):
        for key in self.matches.get(match, []):
            yield key

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True

    def pipeline(self, transaction: bool = False) -> _PipelineStub:
        assert transaction is False
        pipe = _PipelineStub()
        self.pipeline_instances.append(pipe)
        return pipe


def _build_handler(
    monkeypatch: pytest.MonkeyPatch,
    redis_client: object,
    *,
    graph: object | None = None,
) -> OrchestratorGraphHandler:
    graph_stub = graph or _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph_stub,
    )
    return OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=redis_client,
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_apply_session_ttl_batches_expire_calls_with_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    thread_id = "telegram:2348000000001"
    redis_client = _RedisStub(
        matches={
            f"checkpoint:{thread_id}:*": [f"checkpoint:{thread_id}:a", f"checkpoint:{thread_id}:b"],
            f"checkpoint_write:{thread_id}:*": [f"checkpoint_write:{thread_id}:a"],
            f"write_keys_zset:{thread_id}:*": [],
            f"checkpoint_latest:{thread_id}:*": [f"checkpoint_latest:{thread_id}:a"],
        }
    )
    handler = _build_handler(monkeypatch, redis_client)

    ok = await handler._apply_session_ttl(thread_id, ttl=123)

    assert ok is True
    assert redis_client.expire_calls == []
    assert len(redis_client.pipeline_instances) == 3
    assert redis_client.pipeline_instances[0].expire_calls == [
        (f"checkpoint:{thread_id}:a", 123),
        (f"checkpoint:{thread_id}:b", 123),
    ]
    assert redis_client.pipeline_instances[1].expire_calls == [
        (f"checkpoint_write:{thread_id}:a", 123),
    ]
    assert redis_client.pipeline_instances[2].expire_calls == [
        (f"checkpoint_latest:{thread_id}:a", 123),
    ]


@pytest.mark.asyncio
async def test_maybe_apply_session_ttl_only_refreshes_chat_history_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _build_handler(monkeypatch, _RedisStub())
    handler._apply_chat_history_ttl = AsyncMock(return_value=True)
    handler._apply_session_ttl = AsyncMock(return_value=True)

    ok = await handler._maybe_apply_session_ttl("telegram:2348000000001")

    assert ok is True
    handler._apply_chat_history_ttl.assert_awaited_once()
    handler._apply_session_ttl.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_retains_idle_thread_with_fresh_context_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _build_handler(monkeypatch, _RedisStub())
    handler._apply_session_ttl = AsyncMock(return_value=True)
    handler.checkpointer.adelete_thread = AsyncMock()
    now = int(time.time())
    state = {
        "tasks": {},
        "waves": [],
        "pending_interrupt": None,
        "stashed_sessions": [],
        "context_frames": [
            ContextFrame(
                frame_id="beneficiaries_recent",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    }

    ok = await handler._cleanup_if_idle("telegram:2348000000001", state)

    assert ok is True
    handler._apply_session_ttl.assert_awaited_once()
    ttl = handler._apply_session_ttl.await_args.kwargs["ttl"]
    assert 1 <= ttl <= 600
    handler.checkpointer.adelete_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoke_allows_different_threads_to_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _ConcurrentGraphStub()
    handler = _build_handler(monkeypatch, _RedisStub(), graph=graph)
    handler._run_housekeeping = AsyncMock(return_value=None)

    await asyncio.gather(
        handler.invoke(
            MessageContext(
                phone_number="2348000000001",
                text="show balance",
                message_id="msg-1",
                channel="telegram",
            )
        ),
        handler.invoke(
            MessageContext(
                phone_number="2348000000002",
                text="show balance",
                message_id="msg-2",
                channel="telegram",
            )
        ),
    )

    assert graph.max_active_calls == 2
    assert sorted(graph.thread_ids) == ["telegram:2348000000001", "telegram:2348000000002"]
    assert handler._thread_locks == {}
    assert handler._thread_lock_refcounts == {}


@pytest.mark.asyncio
async def test_invoke_serializes_same_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _ConcurrentGraphStub(delay=0.01)
    handler = _build_handler(monkeypatch, _RedisStub(), graph=graph)
    handler._run_housekeeping = AsyncMock(return_value=None)

    await asyncio.gather(
        handler.invoke(
            MessageContext(
                phone_number="2348000000001",
                text="show balance",
                message_id="msg-1",
                channel="telegram",
            )
        ),
        handler.invoke(
            MessageContext(
                phone_number="2348000000001",
                text="show transactions",
                message_id="msg-2",
                channel="telegram",
            )
        ),
    )

    assert graph.max_active_calls == 1
    assert graph.thread_ids == ["telegram:2348000000001", "telegram:2348000000001"]
    assert handler._thread_locks == {}
    assert handler._thread_lock_refcounts == {}
