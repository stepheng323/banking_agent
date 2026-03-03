from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_query_task,
)
from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage, TransactionOutcome, TransactionResult
from apps.core.src.agent.orchestrator.models.state import OrchestratorState


class _DummyQueryWorker:
    async def run(self, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        del payload, context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "query_transfer_handoff": {
                    "action": "send_money",
                    "amount": 5000.0,
                    "recipient_name": "Tolu",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                    "recipient_bank_code": "999992",
                    "skip_extraction": True,
                },
            },
        )


@pytest.mark.asyncio
async def test_query_handoff_injects_transfer_task_and_wave() -> None:
    query_task = TaskSpec(
        id="t1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list", "message": "resend it"},
    )
    state = OrchestratorState(
        user_id="u_handoff_1",
        phone_number="2348000000001",
        channel="telegram",
        last_message_text="resend it",
        loaded_context={"language": "en", "user_id": "u_handoff_1", "accounts": []},
        tasks={"t1": query_task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    agg = ExecutionAggregation(state.tasks)
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"query": _DummyQueryWorker()},
        current_wave_len=1,
        agg=agg,
    )

    await handle_query_task(query_task, "t1", ctx)

    assert query_task.stage == TaskStage.COMPLETED

    tasks = agg.updates["tasks"]
    assert "query_handoff_transfer_1" in tasks
    transfer_task = tasks["query_handoff_transfer_1"]
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 5000.0
    assert transfer_task.payload["recipient_name"] == "Tolu"
    assert transfer_task.payload["recipient_account"] == "8162511023"

    waves = agg.updates["waves"]
    assert waves == [["t1"], ["query_handoff_transfer_1"]]
