from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.query import QueryTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.models.domain import (
    Filters,
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
        {"type": "heading", "text": "Debit Transactions — Today"},
        {"type": "heading", "text": today_str},
        {"type": "text", "text": "₦30,000 • Sent to Mum\nWema • ···2221"},
        {"type": "text", "text": "Showing 1-1 of 1"},
    ]
