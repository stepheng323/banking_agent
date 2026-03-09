import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    TaskSpec,
    TaskStage,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        return self._output


@pytest.mark.asyncio
async def test_plan_tasks_updates_amount_on_same_intent() -> None:
    # 1. State: Active transfer for 10,000, already at confirmation stage.
    state = OrchestratorState(
        user_id="u1",
        phone_number="2348011112222",
        channel="whatsapp",
        last_message_text="Make it 15k",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Mum",
                    "amount": 10000,
                    "idempotency_key": "old-key",
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )

    # 2. Planner Output: Intent is still "transfer", but amount is now 15,000.
    planner_output = PlannerOutput(
        primary_intent="transfer",
        confidence=0.98,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 15000 to Mum",
                parameters=TaskParameters(amount=15000),
            )
        ],
    )

    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
        }
    }

    # 3. Execution: Run the planner node.
    updates = await plan_tasks(state, config)

    # 4. Assertions:
    # - Tasks should be updated.
    # - Amount should be 15,000.
    # - Stage should be reset (usually to EXTRACTED or DRAFT depending on build_task_spec_from_plan_item).
    assert "tasks" in updates
    assert "t1" in updates["tasks"]
    assert updates["tasks"]["t1"].payload["amount"] == 15000
    # Before the fix, this test would fail because plan_tasks would return just {} or locale_updates.
