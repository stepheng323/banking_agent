from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS, MAX_TRANSACTION_BATCH_TASKS
from shared.services.funding.planner import MAX_SOURCE_ACCOUNTS
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _PlannerStub:
    def __init__(self, output: PlannerOutput) -> None:
        self.output = output

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
    ) -> PlannerOutput:
        del phone_number, text, context, prompt_signals
        return self.output


def _transfer_task(index: int) -> PlannedTask:
    return PlannedTask(
        task_id=f"transfer_{index}",
        action="send_money",
        executor="transfer",
        instruction=f"Send money to recipient {index}",
        parameters=TaskParameters(amount=1000, recipient_name=f"Recipient {index}"),
        risk="MONEY_MOVE",
    )


def _planner_output(task_count: int) -> PlannerOutput:
    return PlannerOutput(
        primary_intent="mixed" if task_count > 1 else "transfer",
        response="",
        response_key=None,
        confidence=0.95,
        is_complex=task_count > 1,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        tasks=[_transfer_task(index) for index in range(1, task_count + 1)],
    )


async def _run_planner(output: PlannerOutput) -> dict[str, Any]:
    state = OrchestratorState(
        user_id="u_limits",
        phone_number="2348000000099",
        channel="telegram",
        last_message_text="Send many transfers",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PlannerStub(output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }
    return await plan_tasks(state, config)


def test_pool_funding_source_limit_is_two() -> None:
    assert MAX_SOURCE_ACCOUNTS == MAX_POOLED_SOURCE_ACCOUNTS == 2


@pytest.mark.asyncio
async def test_planner_allows_five_transaction_batch_tasks() -> None:
    updates = await _run_planner(_planner_output(MAX_TRANSACTION_BATCH_TASKS))

    assert "final_response" not in updates
    assert len(updates["tasks"]) == MAX_TRANSACTION_BATCH_TASKS


@pytest.mark.asyncio
async def test_planner_blocks_more_than_five_transaction_batch_tasks() -> None:
    requested_count = MAX_TRANSACTION_BATCH_TASKS + 1

    updates = await _run_planner(_planner_output(requested_count))

    assert updates["final_response"] == (
        f"I can handle up to {MAX_TRANSACTION_BATCH_TASKS} transactions in one batch. "
        f"You asked for {requested_count}. Please send the first {MAX_TRANSACTION_BATCH_TASKS} now, "
        "then I can help with the rest."
    )
    assert updates["routing_decision"] == "transaction_batch_limit"
    assert "tasks" not in updates
    assert "waves" not in updates

