from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage, TransactionOutcome, TransactionResult
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.task_handlers.query import handle_query_task
from apps.chat.src.agent.orchestrator.task_handlers.runtime import ExecutionAggregation, ExecutionContext
from apps.chat.src.agent.shared.query_contracts import (
    FocusedReferent,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import QueryResult


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


class _DummyQueryReferentWorker:
    async def run(self, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        del payload, context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="You last paid Mum on March 24, 2026.",
            patch={
                "query_result": QueryResult(
                    summary_text="accounts:1|showing:1-1|total:1",
                    followup_referent=FocusedReferent(
                        label="Mum",
                        recipient_name="Mum",
                        recipient_account="8162511023",
                        recipient_bank_name="Opay",
                        recipient_bank_code="999992",
                        recipient_resolved_name="Mercy Johnson",
                    ),
                )
            },
        )


class _DummyQuerySurfaceWorker:
    async def run(self, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        del payload, context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Recent transactions\n\n1. Credit from Ada",
            patch={
                "query_result": QueryResult(
                    summary_text="accounts:1|showing:1-1|total:1",
                    surface_view=SurfaceView(
                        mode=SurfaceViewMode.TRANSACTION_LIST,
                        items=[
                            SurfaceItemView(
                                id="tx-surface-1",
                                label="Credit from Ada",
                                amount=5000.0,
                                payload=SelectionPayload(
                                    selection_kind="transaction",
                                    entity_type="transaction",
                                    entity_id="tx-surface-1",
                                    label="Credit from Ada",
                                ),
                                metadata={
                                    "bank_name": "GTBank",
                                    "transaction_type": "credit",
                                    "status": "successful",
                                },
                            )
                        ],
                        context={"type": "transaction_list"},
                    ),
                )
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


@pytest.mark.asyncio
async def test_query_direct_answer_pushes_focused_beneficiary_context_frame() -> None:
    query_task = TaskSpec(
        id="t1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_search", "message": "when last did I pay mum"},
    )
    state = OrchestratorState(
        user_id="u_handoff_2",
        phone_number="2348000000002",
        channel="telegram",
        last_message_text="when last did I pay mum",
        loaded_context={"language": "en", "user_id": "u_handoff_2", "accounts": []},
        tasks={"t1": query_task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    agg = ExecutionAggregation(state.tasks)
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"query": _DummyQueryReferentWorker()},
        current_wave_len=1,
        agg=agg,
    )

    await handle_query_task(query_task, "t1", ctx)

    assert query_task.stage == TaskStage.COMPLETED
    assert "context_frames" in agg.updates
    pushed_frame = agg.updates["context_frames"][-1]
    assert pushed_frame.frame_type == ContextFrameType.BENEFICIARY_LIST
    assert len(pushed_frame.items) == 1
    assert pushed_frame.items[0].label == "Mum"
    assert pushed_frame.items[0].data["account_number"] == "8162511023"
    assert pushed_frame.items[0].focused_referent is not None
    assert pushed_frame.items[0].focused_referent.recipient_resolved_name == "Mercy Johnson"
    assert pushed_frame.items[0].selection_payload is not None
    assert pushed_frame.items[0].selection_payload.selection_kind == "transaction"


@pytest.mark.asyncio
async def test_query_result_surface_view_pushes_transaction_context_frame() -> None:
    query_task = TaskSpec(
        id="t1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list", "message": "show my recent transactions"},
    )
    state = OrchestratorState(
        user_id="u_surface_query_1",
        phone_number="2348000000003",
        channel="telegram",
        last_message_text="show my recent transactions",
        last_message_id="msg-surface-1",
        loaded_context={"language": "en", "user_id": "u_surface_query_1", "accounts": []},
        tasks={"t1": query_task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    agg = ExecutionAggregation(state.tasks)
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"query": _DummyQuerySurfaceWorker()},
        current_wave_len=1,
        agg=agg,
    )

    await handle_query_task(query_task, "t1", ctx)

    assert query_task.stage == TaskStage.COMPLETED
    pushed_frame = agg.updates["context_frames"][-1]
    assert pushed_frame.frame_type == ContextFrameType.TRANSACTION_LIST
    assert pushed_frame.source_message_id == "msg-surface-1"
    assert pushed_frame.items[0].label == "Credit from Ada"
    assert pushed_frame.items[0].data["bank_name"] == "GTBank"
    assert pushed_frame.items[0].selection_payload is not None
