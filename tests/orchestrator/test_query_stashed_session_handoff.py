from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.query import QueryTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.results import TransactionOutcome, TransactionResult


class _QueryWorkerCapturingContext:
    def __init__(self) -> None:
        self.last_context: dict[str, Any] | None = None

    async def run(self, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        del payload
        self.last_context = context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={"session_active": True, "restored_from_stashed_query_session": True},
            response="Restored.",
        )


class _QueryWorkerEndingSession:
    async def run(self, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        del payload, context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={"session_active": False, "_query_session_transition": "end_query_session"},
            response="You're welcome.",
        )


@pytest.mark.asyncio
async def test_handle_query_task_passes_and_clears_stashed_query_session() -> None:
    query_task = TaskSpec(
        id="t_query_stashed",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list", "message": "show transactions"},
    )
    worker = _QueryWorkerCapturingContext()
    stashed_query_session = {"session_active": True, "query_result": {"summary_text": "Earlier summary"}}
    state = OrchestratorState(
        user_id="u_query_stash_ctx_1",
        phone_number="2348000000400",
        channel="whatsapp",
        last_message_text="show transactions",
        loaded_context={"language": "en", "user_id": "u_query_stash_ctx_1", "accounts": []},
        tasks={"t_query_stashed": query_task},
        waves=[["t_query_stashed"]],
        current_wave_index=0,
        stashed_query_session=stashed_query_session,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    agg = ExecutionAccumulator(state.tasks)
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"query": worker}),
        current_wave_len=1,
        accumulator=agg,
    )

    await QueryTaskExecutor().execute(query_task, "t_query_stashed", ctx)

    assert worker.last_context is not None
    assert worker.last_context["stashed_query_session"] == stashed_query_session
    assert agg.to_updates()["stashed_query_session"] is None


@pytest.mark.asyncio
async def test_handle_query_task_pops_active_session_when_query_session_ends() -> None:
    query_task = TaskSpec(
        id="t_query_end",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list", "message": "Okay thanks"},
    )
    state = OrchestratorState(
        user_id="u_query_end_ctx_1",
        phone_number="2348000000401",
        channel="whatsapp",
        last_message_text="Okay thanks",
        loaded_context={"language": "en", "user_id": "u_query_end_ctx_1", "accounts": []},
        tasks={"t_query_end": query_task},
        waves=[["t_query_end"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    agg = ExecutionAccumulator(state.tasks)
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"query": _QueryWorkerEndingSession()}),
        current_wave_len=1,
        accumulator=agg,
    )

    await QueryTaskExecutor().execute(query_task, "t_query_end", ctx)

    updates = agg.to_updates()
    assert updates["session_stack"] == []
    assert "You're welcome." in updates["outbox"][0]["text"]
