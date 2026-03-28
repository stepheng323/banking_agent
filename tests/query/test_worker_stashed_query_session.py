from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ExtractionIntent,
    Filters,
    NormalizedQuery,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    QueryTimeRange,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.services.reasoner import QuerySemanticDecision
from apps.core.src.agent.graphs.query.session import QuerySessionManager, _session_has_surface_view
from apps.core.src.agent.graphs.query.worker import QueryWorker
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.core.src.agent.shared.query_contracts import SurfaceView, SurfaceViewMode


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


class _RecordingProvider:
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        self.transactions = transactions
        self.calls = 0

    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        self.calls += 1
        return list(self.transactions)


class _WindowedProvider:
    def __init__(self, transactions_by_account: dict[str, list[dict[str, Any]]]) -> None:
        self.transactions_by_account = transactions_by_account
        self.calls = 0

    async def get_transactions(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del args, kwargs
        self.calls += 1
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        return [
            transaction
            for transaction in self.transactions_by_account.get(account_id, [])
            if start <= date.fromisoformat(str(transaction["date"])) <= end
        ]


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


class _LoadedSessionManager(_SessionManager):
    def __init__(self, loaded_state: dict[str, Any]) -> None:
        super().__init__()
        self.loaded_state = loaded_state
        self.cleared_key: str | None = None

    async def load(self, key: str) -> dict[str, Any] | None:
        del key
        return dict(self.loaded_state)

    async def clear(self, key: str) -> None:
        self.cleared_key = key


class _ProgressTracker:
    def __init__(self) -> None:
        self.stage_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def set_stage(self, stage_key: str, *, stage_metadata: dict[str, Any] | None = None) -> None:
        self.stage_calls.append((stage_key, stage_metadata))


class _RedisStoreStub:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | None:
        return self.saved.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.saved[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.saved.pop(key, None)
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


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
async def test_worker_does_not_restore_stale_stashed_query_session() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    stashed_query_session = {
        "session_active": True,
        "timestamp": 0.0,
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
        assert state["query_session"] == {}
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "any credits?"},
        context={
            "phone_number": "2348000000308",
            "user_id": "u1",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 4),
            "stashed_query_session": stashed_query_session,
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch is not None
    assert result.patch.get("restored_from_stashed_query_session") is None


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
async def test_worker_appends_recent_query_frame_history_on_successful_query() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    query_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
        )
    )
    query_result = QueryResult(
        summary_text="You spent ₦60,000 this week.",
        interpretation={"intent": "analytics_summary"},
        items=[
            QueryResultItem(
                id="txn-1",
                description="Transfer to Mum",
                amount=50000.0,
                date=date(2026, 3, 18),
            ),
            QueryResultItem(
                id="txn-2",
                description="Transfer to Dad",
                amount=10000.0,
                date=date(2026, 3, 17),
            ),
        ],
        surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context={"type": "spending_total"}),
    )

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "query_contract": query_contract,
                "query_result": query_result,
                "flow_state": "complete",
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How much did I spend this week"},
        context={
            "phone_number": "2348000000302",
            "user_id": "u3",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert session_manager.saved_state is not None
    frames = session_manager.saved_state.get("query_frames")
    assert isinstance(frames, list)
    assert len(frames) == 1
    assert frames[0].frame_id == "qf_1"
    assert frames[0].facts.amount == 60000.0
    assert "surface" not in session_manager.saved_state


def test_session_shape_detects_surface_view() -> None:
    session = {
        "session_active": True,
        "query_result": QueryResult(
            summary_text="You spent ₦60,000 this week.",
            surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY),
        ).model_dump(mode="json"),
    }

    assert _session_has_surface_view(session) is True


@pytest.mark.asyncio
async def test_worker_sets_followup_progress_stage_before_pipeline_run() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    tracker = _ProgressTracker()
    stashed_query_session = {
        "session_active": True,
        "query_contract": QueryExecutionContract(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_start=date(2026, 3, 16),
            time_end=date(2026, 3, 19),
            filters={"transaction_type": "debit", "merchant": ["mum"]},
            normalized_query=NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
                filters={"transaction_type": "debit", "merchant": ["mum"]},
            ),
        ).model_dump(),
        "current_page": 0,
    }

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        assert tracker.stage_calls == [
            (
                "query.resolving_followup",
                {
                    "intent_family": "analytics_summary",
                    "direction": "sent",
                    "counterparty_label": "mum",
                    "scope_label": "what you sent to mum",
                },
            )
        ]
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000399",
            "user_id": "u-progress",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
            "stashed_query_session": stashed_query_session,
            "progress_tracker": tracker,
        },
    )

    assert result.outcome == TransactionOutcome.OK


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
            "session_source": "none",
            "restored_from_stashed_query_session": False,
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
                "query_result": QueryResult(
                    summary_text="",
                    surface_view=SurfaceView(
                        mode=SurfaceViewMode.DIRECT_ANSWER,
                        context={"type": "single_transaction"},
                    ),
                ),
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
                    ],
                    "surface_view": {"mode": "direct_answer", "context": {"type": "single_transaction"}},
                },
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
                "query_result": QueryResult(
                    summary_text="You spent ₦10,000 yesterday.",
                    surface_view=SurfaceView(
                        mode=SurfaceViewMode.GROUPED_SUMMARY,
                        context={"type": "spending_total"},
                    ),
                ),
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
                "query_result": {
                    "summary_text": "You spent ₦10,000 yesterday.",
                    "surface_view": {"mode": "grouped_summary", "context": {"type": "spending_total"}},
                },
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


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary_from_surface_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                "flow_state": "complete",
                "query_result": QueryResult(
                    summary_text="You spent ₦10,000 this week.",
                    surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY),
                ),
                "_query_semantic_decision": "fresh_query",
                "_query_semantic_context_mode": "none",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": None,
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "how much did i spend"},
        context={
            "phone_number": "2348000000305",
            "user_id": "u6",
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
            "flow_state": "complete",
            "surface_type": "summary",
            "session_active": True,
            "has_pending_clarification": False,
            "session_source": "none",
            "restored_from_stashed_query_session": False,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_restored_stashed_query_session_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    stashed_query_session = {
        "session_active": True,
        "query_contract": QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            )
        ).model_dump(),
        "query_result": {
            "summary_text": "You spent ₦60,000 this week.",
            "items": [],
            "surface_view": {"mode": "grouped_summary", "context": {"type": "spending_total"}},
        },
        "query_frames": [],
    }

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000310",
            "user_id": "u-log-shape",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
            "stashed_query_session": stashed_query_session,
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_session_restored_from_stash",
        {
            "session_source": "stashed",
            "session_active": True,
            "has_query_contract": True,
            "has_query_result": True,
            "has_surface": True,
            "has_query_frames": False,
            "has_pending_clarification": False,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_warns_when_loaded_active_session_is_missing_query_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _LoadedSessionManager(
        {
            "session_active": True,
            "query_result": {
                "summary_text": "Earlier summary",
                "items": [],
                "surface_view": {"mode": "grouped_summary", "context": {"type": "spending_total"}},
            },
            "query_frames": [],
        }
    )
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    warnings: list[tuple[str, dict[str, Any]]] = []

    def _capture_warning(event: str, **kwargs: Any) -> None:
        warnings.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.worker.logger.warning", _capture_warning)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"] == {}
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": False})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000311",
            "user_id": "u-missing-contract",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert session_manager.cleared_key == "query:session:2348000000311"
    assert (
        "query_session_missing_contract_cleared",
        {
            "session_source": "redis",
            "session_active": True,
            "has_query_result": True,
            "has_surface": True,
            "has_query_frames": False,
        },
    ) in warnings


@pytest.mark.asyncio
async def test_worker_recovers_ambiguous_last_week_followup_from_stashed_session() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    stashed_query_session = {
        "session_active": True,
        "query_contract": QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
                aggregation=Aggregation(type="sum"),
            )
        ).model_dump(),
        "query_result": {"summary_text": "You spent ₦60,000 on mum this week.", "items": []},
        "current_page": 1,
        "show_expanded": True,
    }

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.21,
            reason="ambiguous_followup",
        )

    captured_ranges: list[tuple[date, date]] = []

    async def _fake_execute(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        query_contract = state["query_contract"]
        if isinstance(query_contract, dict):
            query_contract = QueryExecutionContract.model_validate(query_contract)
        captured_ranges.append((query_contract.time_start, query_contract.time_end))
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Recovered last week summary.",
            patch={
                "session_active": True,
                "query_contract": query_contract,
                "query_result": QueryResult(summary_text="Recovered last week summary.", items=[]),
                "flow_state": "complete",
            },
        )

    worker.extractor.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    worker.executor.run = _fake_execute  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000312",
            "user_id": "u-worker-recovery",
            "accounts": [{"account_id": "acc_1"}],
            "language": "en",
            "today": date(2026, 3, 19),
            "stashed_query_session": stashed_query_session,
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert captured_ranges == [(date(2026, 3, 9), date(2026, 3, 15))]


@pytest.mark.asyncio
async def test_worker_reuses_active_query_scope_for_how_much_total_followup() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    stashed_query_session = {
        "session_active": True,
        "query_contract": QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 19), granularity="month"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
                result_limit=5,
                result_reference="latest",
            )
        ).model_dump(),
        "query_result": {"summary_text": "Transactions to Mum this month.", "items": []},
        "current_page": 0,
        "show_expanded": False,
    }

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="llm_total_followup",
        )

    captured_contracts: list[QueryExecutionContract] = []

    async def _fake_execute(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        query_contract = state["query_contract"]
        if isinstance(query_contract, dict):
            query_contract = QueryExecutionContract.model_validate(query_contract)
        captured_contracts.append(query_contract)
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Total sent to Mum this month.",
            patch={
                "session_active": True,
                "query_contract": query_contract,
                "query_result": QueryResult(summary_text="Total sent to Mum this month.", items=[]),
                "flow_state": "complete",
            },
        )

    worker.extractor.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    worker.executor.run = _fake_execute  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How much total"},
        context={
            "phone_number": "2348000000313",
            "user_id": "u-worker-total",
            "accounts": [{"account_id": "acc_1"}],
            "language": "en",
            "today": date(2026, 3, 19),
            "stashed_query_session": stashed_query_session,
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert len(captured_contracts) == 1
    query_contract = captured_contracts[0]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == date(2026, 3, 19)
    assert query_contract.normalized_query.filters is not None
    assert query_contract.normalized_query.filters.transaction_type == "debit"
    assert query_contract.normalized_query.filters.merchant == ["mum"]
    assert query_contract.normalized_query.aggregation is not None
    assert query_contract.normalized_query.aggregation.type == "sum"


@pytest.mark.asyncio
async def test_worker_reuses_persisted_cached_transactions_for_time_delta_followup() -> None:
    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _RecordingProvider(
        transactions=[
            {
                "id": "tx-old",
                "narration": "Salary payment",
                "amount": 100000,
                "date": "2026-03-01",
                "type": "credit",
            },
            {
                "id": "tx-recent",
                "narration": "Refund",
                "amount": 5000,
                "date": "2026-03-04",
                "type": "credit",
            },
            {
                "id": "tx-debit",
                "narration": "Card payment",
                "amount": 2500,
                "date": "2026-03-04",
                "type": "debit",
            },
        ]
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 4), granularity="day"),
            filters=Filters(transaction_type="credit"),
            result_limit=5,
            result_reference="latest",
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_contract": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "Show my incoming transactions"},
        context={
            "phone_number": "2348000000314",
            "user_id": "u-worker-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 4), end=date(2026, 3, 4), granularity="day"),
            delta_type="time",
            confidence=0.98,
            reason="llm_same_scope_narrower_window",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about today?"},
        context={
            "phone_number": "2348000000314",
            "user_id": "u-worker-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1
    assert second_result.patch is not None
    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert query_result.cache_reused is True
    assert [item.description for item in query_result.items or []] == ["Refund"]


@pytest.mark.asyncio
async def test_worker_reuses_persisted_cached_transactions_for_filter_delta_followup() -> None:
    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _RecordingProvider(
        transactions=[
            {
                "id": "tx-credit",
                "narration": "Salary payment",
                "amount": 100000,
                "date": "2026-03-04",
                "type": "credit",
            },
            {
                "id": "tx-debit",
                "narration": "Card payment",
                "amount": 2500,
                "date": "2026-03-04",
                "type": "debit",
            },
        ]
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 4), granularity="day"),
            result_limit=5,
            result_reference="latest",
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_contract": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "Show my transactions"},
        context={
            "phone_number": "2348000000317",
            "user_id": "u-worker-filter-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="filter_delta",
            followup_intent="refine_existing",
            delta_type="filter",
            filters=Filters(transaction_type="credit"),
            confidence=0.98,
            reason="llm_credit_filter_followup",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "Only credits"},
        context={
            "phone_number": "2348000000317",
            "user_id": "u-worker-filter-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1
    assert second_result.patch is not None
    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert query_result.cache_reused is True
    assert [item.description for item in query_result.items or []] == ["Salary payment"]


@pytest.mark.asyncio
async def test_worker_restores_persisted_analytics_followup_for_time_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.core.src.agent.graphs.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 19))

    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _WindowedProvider(
        {
            "acc_1": [
                {"id": "tx-1", "narration": "Card purchase", "amount": 5000, "date": "2026-03-19", "type": "debit"},
                {"id": "tx-2", "narration": "Fuel", "amount": 7000, "date": "2026-03-18", "type": "debit"},
                {"id": "tx-3", "narration": "Salary", "amount": 95000, "date": "2026-03-17", "type": "credit"},
            ]
        }
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 19), granularity="month"),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_contract": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "How much did I spend this month?"},
        context={
            "phone_number": "2348000000315",
            "user_id": "u-worker-analytics",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 19), end=date(2026, 3, 19), granularity="day"),
            delta_type="time",
            confidence=0.98,
            reason="llm_today_followup",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about today?"},
        context={
            "phone_number": "2348000000315",
            "user_id": "u-worker-analytics",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 2
    assert second_result.patch is not None
    query_contract = second_result.patch["query_contract"]
    assert isinstance(query_contract, QueryExecutionContract)
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 19)
    assert query_contract.time_end == date(2026, 3, 19)

    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert query_result.summary_text == "You spent *₦5,000* today, across 1 transaction."
    assert [item.description for item in query_result.items or []] == ["Card purchase"]


@pytest.mark.asyncio
async def test_worker_restores_persisted_time_comparison_followup_for_time_delta() -> None:
    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _WindowedProvider(
        {
            "acc_1": [
                {"id": "cmp-1", "narration": "Groceries", "amount": 2000, "date": "2026-03-03", "type": "debit"},
                {"id": "cmp-2", "narration": "Fuel", "amount": 3000, "date": "2026-03-05", "type": "debit"},
                {"id": "cur-1", "narration": "Travel", "amount": 6000, "date": "2026-03-10", "type": "debit"},
                {"id": "cur-2", "narration": "Food", "amount": 2000, "date": "2026-03-12", "type": "debit"},
                {"id": "init-cmp", "narration": "Bills", "amount": 1000, "date": "2026-03-13", "type": "debit"},
                {"id": "init-cur", "narration": "Transfer", "amount": 4000, "date": "2026-03-17", "type": "debit"},
            ]
        }
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.TIME_COMPARISON,
            time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            filters=Filters(transaction_type="debit"),
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_contract": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "Compare this week to the previous period"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-comparison",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 2

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 15), granularity="week"),
            delta_type="time",
            confidence=0.98,
            reason="llm_last_week_comparison_followup",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about last week?"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-comparison",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 4
    assert second_result.patch is not None
    query_contract = second_result.patch["query_contract"]
    assert isinstance(query_contract, QueryExecutionContract)
    assert query_contract.intent == QueryIntent.TIME_COMPARISON
    assert query_contract.time_start == date(2026, 3, 9)
    assert query_contract.time_end == date(2026, 3, 15)

    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    spending_item = next(item for item in query_result.items or [] if item.id == "spending")
    assert spending_item.metadata == {
        "current": 9000.0,
        "comparison": 5000.0,
        "change": 4000.0,
        "pct_change": 80.0,
    }
