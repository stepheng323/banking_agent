from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    NormalizedQuery,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryTimeRange,
    TimeRange,
    TimeReference,
)
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
    stashed_query_session = {
        "session_active": True,
        "query_contract": QueryExecutionContract(
            intent=QueryIntent.TRANSACTION_LIST,
            time_start=date(2026, 3, 1),
            time_end=date(2026, 3, 6),
            normalized_query=NormalizedQuery(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            ),
        ).model_dump(),
        "current_page": 0,
    }

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


@pytest.mark.asyncio
async def test_worker_ignores_legacy_stashed_query_session_without_contract() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    stashed_query_session = {
        "session_active": True,
        "query": {
            "intent": "transaction_list",
            "time_range": {"start": "2026-03-01", "end": "2026-03-06", "granularity": "day"},
        },
        "current_page": 0,
    }

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"] == {}
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    await worker.run(
        payload={"message": "show my transactions"},
        context={
            "phone_number": "2348000000300",
            "user_id": "u1",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 6),
            "stashed_query_session": stashed_query_session,
        },
    )

    assert session_manager.saved_state is not None


@pytest.mark.asyncio
async def test_worker_persists_pending_query_clarification_session() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    pending = PendingClarificationState(
        original_query="How much did I spend last",
        current_intent=ExtractionIntent.SPENDING_TOTAL,
        original_extraction=QueryExtractionResult(
            intent=ExtractionIntent.SPENDING_TOTAL,
            time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
            raw_query="How much did I spend last",
        ),
        resolver_message="What time period did you mean by 'last'?",
        language="en",
    )

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            response="What time period did you mean by 'last'?",
            patch={"session_active": True, "pending_clarification": pending},
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How much did I spend last"},
        context={
            "phone_number": "2348000000301",
            "user_id": "u2",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
        },
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert session_manager.saved_state is not None
    assert session_manager.saved_state["session_active"] is True
    assert session_manager.saved_state["pending_clarification"] == pending
