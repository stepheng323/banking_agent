from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.task_handlers.query import handle_query_task
from apps.chat.src.agent.orchestrator.workflows.execution.runtime import (
    ExecutionAccumulator,
    ExecutionServices,
    ExecutionTurnContext,
)
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
        services=ExecutionServices.from_mapping({"query": worker}),
        current_wave_len=1,
        accumulator=agg,
    )

    await handle_query_task(query_task, "t_query_stashed", ctx)

    assert worker.last_context is not None
    assert worker.last_context["stashed_query_session"] == stashed_query_session
    assert agg.updates["stashed_query_session"] is None
