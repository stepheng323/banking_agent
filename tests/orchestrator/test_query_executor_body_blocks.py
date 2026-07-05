from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.query import QueryTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.domain import (
    Filters,
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.utils.timezone import lagos_today


class _InjectedQueryWorker:
    def __init__(self, query_result: QueryResult) -> None:
        self.query_result = query_result
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        self.calls.append({"payload": payload.copy(), "context": context.copy()})
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="fallback query response",
            patch={"query_result": self.query_result},
        )


def _query_contract() -> QueryExecutionContract:
    today = lagos_today()
    return QueryExecutionContract.from_query_ir(
        QueryIR(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit"),
            time_range=TimeRange(start=today, end=today),
        )
    )


def _query_context_frame() -> ContextFrame:
    contract = _query_contract()
    return ContextFrame(
        frame_id="query_surface_1",
        frame_type=ContextFrameType.TRANSACTION_DETAIL,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx1",
                label="Money sent",
                selection_payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx1",
                    label="Money sent",
                    fact_capabilities=["date", "reference"],
                ),
                data={"amount": 30000, "date": lagos_today().isoformat(), "bank_name": "Wema"},
            )
        ],
        created_at_ts=1_771_000_000,
        ttl_seconds=600_000_000,
        metadata={
            "source": "query",
            "surface_mode": "direct_answer",
            "summary_text": "Money sent",
            "query_contract": contract.model_dump(mode="json"),
            "surface_context": {"mode": "direct_answer", "type": "single_transaction"},
        },
    )




async def test_query_executor_attaches_mobile_body_blocks_to_say_outbox() -> None:
    task = TaskSpec(
        id="query_1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        loaded_context={"user_id": "user-1", "language": "en"},
        tasks={"query_1": task},
    )
    query_result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Money sent",
                amount=30000,
                date=lagos_today(),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "counterparty": "Mum",
                    "bank_name": "Wema",
                    "recipient_account": "8067892221",
                },
            )
        ],
        query_contract=_query_contract(),
    )
    worker = _InjectedQueryWorker(query_result)
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"query": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await QueryTaskExecutor().execute(task, "query_1", ctx)

    assert len(worker.calls) == 1
    assert task.stage == TaskStage.COMPLETED
    assert task.payload["result"] == "fallback query response"
    outbox = ctx.accumulator.to_updates()["outbox"]
    assert outbox[0]["text"] == "fallback query response"
    today_str = lagos_today().strftime("%b %d").replace(" 0", " ")
    assert outbox[0]["body_blocks"] == [
        {"type": "text", "text": "I found one debit transaction today."},
        {"type": "text", "text": f"*{today_str}*\n• ₦30,000 — Sent to Mum · Wema · ···2221"},
    ]


async def test_query_executor_passes_active_query_surface_context_to_worker() -> None:
    task = TaskSpec(
        id="query_1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        loaded_context={"user_id": "user-1", "language": "en"},
        context_frames=[_query_context_frame()],
        tasks={"query_1": task},
    )
    query_result = QueryResult(
        summary_text="accounts:1|showing:0-0|total:0",
        items=[],
        query_contract=_query_contract(),
    )
    worker = _InjectedQueryWorker(query_result)
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"query": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await QueryTaskExecutor().execute(task, "query_1", ctx)

    assert len(worker.calls) == 1
    active_session = worker.calls[0]["context"]["active_query_session"]
    assert active_session["session_active"] is True
    assert active_session["query_contract"]["intent"] == "transaction_list"
    assert active_session["query_result"]["surface_view"]["mode"] == "direct_answer"


async def test_query_executor_does_not_attach_body_blocks_for_empty_direct_answer() -> None:
    task = TaskSpec(
        id="query_1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_list"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        loaded_context={"user_id": "user-1", "language": "en"},
        tasks={"query_1": task},
    )
    query_result = QueryResult(
        summary_text="accounts:1|showing:1-0|total:0",
        items=[],
        query_contract=_query_contract(),
    )
    worker = _InjectedQueryWorker(query_result)
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"query": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await QueryTaskExecutor().execute(task, "query_1", ctx)

    outbox = ctx.accumulator.to_updates()["outbox"]
    assert outbox[0]["text"] == "fallback query response"
    assert "body_blocks" not in outbox[0]


async def test_query_executor_does_not_attach_body_blocks_for_direct_fact_answer() -> None:
    task = TaskSpec(
        id="query_1",
        type="query",
        stage=TaskStage.DRAFT,
        payload={"action": "transaction_detail"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        loaded_context={"user_id": "user-1", "language": "en"},
        tasks={"query_1": task},
    )
    query_result = QueryResult(
        summary_text="The reference is txn_010.",
        items=[
            QueryResultItem(
                id="tx1",
                description="Netflix Monthly Subscription",
                amount=6500,
                date=lagos_today(),
                metadata={"type": "debit", "counterparty": "Netflix", "reference": "txn_010"},
            )
        ],
        query_contract=QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.TRANSACTION_DETAIL,
                time_range=TimeRange(start=lagos_today(), end=lagos_today()),
                answer_fact_field="reference",
            )
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
        answer_context=QueryAnswerContext(primary_text="The reference is txn_010."),
    )
    worker = _InjectedQueryWorker(query_result)
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"query": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await QueryTaskExecutor().execute(task, "query_1", ctx)

    outbox = ctx.accumulator.to_updates()["outbox"]
    assert outbox[0]["text"] == "fallback query response"
    assert "body_blocks" not in outbox[0]
