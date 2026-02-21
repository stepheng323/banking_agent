"""Interrupt-node tests for multilingual stash-and-switch behavior."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.interrupt import handle_pending_interrupt
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        return self._output


@pytest.mark.asyncio
async def test_interrupt_input_stashes_transfer_and_switches_to_beneficiary() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_1",
        phone_number="2348011111111",
        channel="whatsapp",
        last_message_text="fi awon beneficiary mi han",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["recipient_account"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu", "amount": 5000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner_output = PlannerOutput(
        primary_intent="beneficiary",
        response="",
        confidence=0.93,
        detected_language="Yoruba",
        normalized_instruction="show beneficiaries",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="list_beneficiaries",
                executor="beneficiary",
                instruction="List saved beneficiaries",
                parameters=TaskParameters(),
                risk="READ_ONLY",
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": _MockPlanner(planner_output)}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert len(updates["stashed_sessions"]) == 1
    assert updates["stashed_sessions"][0]["intent"] == "transfer"
    assert updates["waves"] == [["t1"]]
    assert updates["tasks"]["t1"].type == "beneficiary"


@pytest.mark.asyncio
async def test_interrupt_input_same_executor_keeps_slot_filling_flow() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_2",
        phone_number="2348022222222",
        channel="whatsapp",
        last_message_text="use gtbank",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_bank_name"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.RESOLVED,
                payload={"recipient_name": "Tolu", "idempotency_key": "old-key"},
            )
        },
    )
    planner_output = PlannerOutput(
        primary_intent="transfer",
        response="",
        confidence=0.87,
        detected_language="English",
        normalized_instruction="use gtbank",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Update transfer bank details",
                parameters=TaskParameters(bank_name="GTBank"),
                risk="MONEY_MOVE",
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": _MockPlanner(planner_output)}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload
    assert "stashed_sessions" not in updates
