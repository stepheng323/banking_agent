from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.worker import QueryWorker
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise NotImplementedError


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


class _DummyProvider:
    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []


class _SessionManager:
    def __init__(self) -> None:
        self.saved_state: dict[str, Any] | None = None

    async def load(self, key: str) -> dict[str, Any] | None:
        del key
        return None

    async def save(self, key: str, state: dict[str, Any]) -> None:
        del key
        self.saved_state = state

    async def clear(self, key: str) -> None:
        del key


@pytest.mark.asyncio
async def test_worker_restores_from_stashed_query_session_and_marks_patch() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    stashed_query_session = {"session_active": True, "query": {"intent": "transaction_list"}, "current_page": 0}

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"] == stashed_query_session
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "any credits?"},
        context={
            "phone_number": "2348000000300",
            "user_id": "u1",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 4),
            "stashed_query_session": stashed_query_session,
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["restored_from_stashed_query_session"] is True
    assert session_manager.saved_state is not None
