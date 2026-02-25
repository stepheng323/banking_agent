"""Interrupt routing call-budget tests."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.interrupt import handle_pending_interrupt
from shared.types.planner import InterruptRouteDecision, PlannedTask, PlannerOutput, TaskParameters


class _CountingPlanner:
    def __init__(self, route: InterruptRouteDecision, output: PlannerOutput) -> None:
        self._route = route
        self._output = output
        self.route_calls = 0
        self.plan_calls = 0

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        self.route_calls += 1
        return self._route

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        self.plan_calls += 1
        return self._output


@pytest.mark.asyncio
async def test_direct_switch_target_skips_planner_call() -> None:
    state = OrchestratorState(
        user_id="u_budget_1",
        phone_number="2348100000001",
        channel="whatsapp",
        last_message_text="what is my balance",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_bank_name"]},
        ),
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
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.94,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="explicit account request",
        ),
        output=PlannerOutput(primary_intent="account"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    task_ids = list(updates["tasks"].keys())
    assert len(task_ids) == 1
    assert updates["tasks"][task_ids[0]].type == "account"
    assert updates["tasks"][task_ids[0]].payload["message"] == "what is my balance"


@pytest.mark.asyncio
async def test_transaction_switch_target_invokes_planner_call() -> None:
    state = OrchestratorState(
        user_id="u_budget_2",
        phone_number="2348100000002",
        channel="whatsapp",
        last_message_text="send 8k to tolu",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["amount"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mercy", "amount": 5000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.96,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="fresh transfer request",
        ),
        output=PlannerOutput(
            primary_intent="transfer",
            tasks=[
                PlannedTask(
                    task_id="t2",
                    action="send_money",
                    executor="transfer",
                    instruction="Send 8000 to Tolu",
                    parameters=TaskParameters(amount=8000, recipient="Tolu"),
                    risk="MONEY_MOVE",
                )
            ],
        ),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 1
    assert list(updates["tasks"].keys()) == ["t2"]
    assert updates["tasks"]["t2"].type == "transfer"


@pytest.mark.asyncio
async def test_confirmation_shortcut_skips_router_and_planner_calls() -> None:
    state = OrchestratorState(
        user_id="u_budget_3",
        phone_number="2348100000003",
        channel="whatsapp",
        last_message_text="proceed",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"amount": 5000, "recipient_name": "Tolu"},
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="cancel",
            confidence=0.4,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="should_not_run",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_AUTH


@pytest.mark.asyncio
async def test_status_shortcut_skips_router_and_planner_calls() -> None:
    state = OrchestratorState(
        user_id="u_budget_4",
        phone_number="2348100000004",
        channel="whatsapp",
        last_message_text="where did we stop",
        loaded_context={"language": "en"},
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
                payload={"recipient_name": "Tolu", "amount": 5000},
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.4,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="should_not_run",
        ),
        output=PlannerOutput(primary_intent="account"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is not None
    assert updates["outbox"][0]["type"] == "say"


@pytest.mark.asyncio
async def test_auth_yes_text_still_uses_router_path() -> None:
    state = OrchestratorState(
        user_id="u_budget_5",
        phone_number="2348100000005",
        channel="whatsapp",
        last_message_text="yes",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter your PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_name": "Tolu", "amount": 5000},
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="unclear",
            confidence=0.6,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="auth_router_path",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is not None
