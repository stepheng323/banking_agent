"""Dependency-wave planning and execution tests."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import AccountOutcome, AccountResult, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _AccountWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> AccountResult:
        del payload, context, user_message, pin_verified
        return AccountResult(outcome=AccountOutcome.OK, response="balance")


@pytest.mark.asyncio
async def test_planner_builds_dependency_aware_waves_for_mixed_request() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10000 to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10000 to Dad",
                parameters=TaskParameters(amount=10000, recipient="Dad"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t3",
                action="check_balance",
                executor="account",
                instruction="Show my balance",
                parameters=TaskParameters(),
                depends_on=["t1", "t2"],
                risk="READ_ONLY",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_1",
        phone_number="2348111111101",
        channel="whatsapp",
        last_message_text="send 10k to mum and dad then show my balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["waves"] == [["t1", "t2"], ["t3"]]


@pytest.mark.asyncio
async def test_planner_fans_out_single_transfer_when_text_has_multiple_recipients() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=False,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_1",
        phone_number="2348111111191",
        channel="whatsapp",
        last_message_text="send 10k to mum and tolu",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t1_r2"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "mum"
    assert updates["tasks"]["t1_r2"].payload.get("recipient_name") == "tolu"
    assert updates["tasks"]["t1"].payload.get("amount") == 10000
    assert updates["tasks"]["t1_r2"].payload.get("amount") == 10000
    assert updates["waves"] == [["t1", "t1_r2"]]


@pytest.mark.asyncio
async def test_planner_fanout_rewrites_downstream_dependencies() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="check_balance",
                executor="account",
                instruction="Show my balance",
                parameters=TaskParameters(),
                depends_on=["t1"],
                risk="READ_ONLY",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_2",
        phone_number="2348111111192",
        channel="whatsapp",
        last_message_text="send 10k to mum and tolu then show my balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t1_r2", "t2"}
    assert updates["tasks"]["t2"].depends_on == ["t1", "t1_r2"]
    assert updates["waves"] == [["t1", "t1_r2"], ["t2"]]


@pytest.mark.asyncio
async def test_planner_does_not_fanout_when_planner_already_emits_multiple_transfer_tasks() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Tolu",
                parameters=TaskParameters(amount=10000, recipient="Tolu"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_3",
        phone_number="2348111111193",
        channel="whatsapp",
        last_message_text="send 10k to mum and tolu",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t2"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["t2"].payload.get("recipient_name") == "Tolu"
    assert updates["waves"] == [["t1", "t2"]]


@pytest.mark.asyncio
async def test_dependent_task_cancelled_when_dependency_failed() -> None:
    state = OrchestratorState(
        user_id="u_dep_2",
        phone_number="2348111111102",
        channel="whatsapp",
        waves=[["t3"]],
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.FAILED, payload={}),
            "t2": TaskSpec(id="t2", type="transfer", stage=TaskStage.COMPLETED, payload={}),
            "t3": TaskSpec(
                id="t3",
                type="account",
                depends_on=["t1", "t2"],
                stage=TaskStage.DRAFT,
                payload={"action": "check_balance"},
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"account": _AccountWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    assert state.tasks["t3"].stage == TaskStage.CANCELLED
    assert updates["current_wave_index"] == 1


@pytest.mark.asyncio
async def test_dependent_task_runs_after_dependencies_completed() -> None:
    state = OrchestratorState(
        user_id="u_dep_3",
        phone_number="2348111111103",
        channel="whatsapp",
        waves=[["t3"]],
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.COMPLETED, payload={}),
            "t2": TaskSpec(id="t2", type="transfer", stage=TaskStage.COMPLETED, payload={}),
            "t3": TaskSpec(
                id="t3",
                type="account",
                depends_on=["t1", "t2"],
                stage=TaskStage.DRAFT,
                payload={"action": "check_balance"},
            ),
        },
        loaded_context={
            "profile": {},
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"account": _AccountWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    assert state.tasks["t3"].stage == TaskStage.COMPLETED
    assert updates["current_wave_index"] == 1
