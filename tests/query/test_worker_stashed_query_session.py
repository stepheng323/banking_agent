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


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "flow_state": "executing",
                "_query_semantic_decision": "fresh_query",
                "_query_semantic_context_mode": "none",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": None,
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "show my last transaction"},
        context={
            "phone_number": "2348000000302",
            "user_id": "u3",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_turn_summary",
        {
            "context_mode": "fresh",
            "semantic_decision": "fresh_query",
            "semantic_context_mode": "none",
            "semantic_llm_used": True,
            "deterministic_surface_action": None,
            "session_transition": None,
            "outcome": "ok",
            "flow_state": "executing",
            "surface_type": None,
            "session_active": True,
            "has_pending_clarification": False,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary_for_active_result_fact_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"]["session_active"] is True
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "flow_state": "executing",
                "surface": {"type": "single_item", "items": [], "context": {"type": "single_transaction"}},
                "_query_semantic_decision": "continuation",
                "_query_semantic_context_mode": "active_result",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": "answer_fact_active_result",
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "was it successful?"},
        context={
            "phone_number": "2348000000303",
            "user_id": "u4",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
            "stashed_query_session": {
                "session_active": True,
                "query_contract": QueryExecutionContract(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_start=date(2026, 3, 13),
                    time_end=date(2026, 3, 13),
                    normalized_query=NormalizedQuery(
                        intent=QueryIntent.TRANSACTION_SEARCH,
                        time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                    ),
                ).model_dump(),
                "query_result": {
                    "items": [
                        {
                            "id": "txn-1",
                            "description": "Payment to Mum",
                            "amount": 10000.0,
                            "date": "2026-03-13",
                            "metadata": {"status": "processing"},
                        }
                    ]
                },
                "surface": {"type": "single_item", "items": [], "context": {"type": "single_transaction"}},
            },
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_session_transition",
        {
            "transition": "answer_fact_active_result",
            "context_mode": "active_result",
            "semantic_decision": "continuation",
            "session_active": True,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary_for_conversational_active_result_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"]["session_active"] is True
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="It is on the high side.\n\nYou can ask what made it up.",
            patch={
                "session_active": False,
                "flow_state": "complete",
                "surface": {"type": "summary", "items": [], "context": {"type": "spending_total"}},
                "_query_semantic_decision": "continuation",
                "_query_semantic_context_mode": "active_result",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": "exit_query_session_conversational",
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "That's a lot"},
        context={
            "phone_number": "2348000000304",
            "user_id": "u5",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
            "stashed_query_session": {
                "session_active": True,
                "query_contract": QueryExecutionContract(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_start=date(2026, 3, 9),
                    time_end=date(2026, 3, 13),
                    normalized_query=NormalizedQuery(
                        intent=QueryIntent.ANALYTICS_SUMMARY,
                        time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                    ),
                ).model_dump(),
                "query_result": {"summary_text": "You spent ₦10,000 yesterday."},
                "surface": {"type": "summary", "items": [], "context": {"type": "spending_total"}},
            },
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_session_transition",
        {
            "transition": "exit_query_session_conversational",
            "context_mode": "active_result",
            "semantic_decision": "continuation",
            "session_active": False,
        },
    ) in events
    assert (
        "query_turn_summary",
        {
            "context_mode": "active_result",
            "semantic_decision": "continuation",
            "semantic_context_mode": "active_result",
            "semantic_llm_used": True,
            "deterministic_surface_action": None,
            "session_transition": "exit_query_session_conversational",
            "outcome": "ok",
            "flow_state": "complete",
            "surface_type": "summary",
            "session_active": False,
            "has_pending_clarification": False,
        },
    ) in events
