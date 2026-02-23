"""Interrupt-node tests for multilingual stash-and-switch behavior."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    AccountResult,
    ActiveSession,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.interrupt import handle_pending_interrupt
from shared.types.planner import InterruptRouteDecision, PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    def __init__(
        self,
        output: PlannerOutput,
        route: InterruptRouteDecision | None = None,
    ) -> None:
        self._output = output
        self._route = route

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        return self._output

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        if self._route is None:
            return InterruptRouteDecision(
                decision="continue_flow",
                confidence=0.5,
                detected_language=None,
                target_intent=None,
                target_mode=None,
                reason="default continue",
            )
        return self._route


class _RouteOnlyPlanner:
    def __init__(self, route: InterruptRouteDecision) -> None:
        self._route = route

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        return self._route

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        raise AssertionError("plan_tasks should not be called for direct switch targets")


class _FailIfRouterCalledPlanner:
    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        raise AssertionError("route_pending_input should not be called for callback auto-approve")

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        raise AssertionError("plan_tasks should not be called for callback auto-approve")


class _AccountBalanceWorker:
    def __init__(self) -> None:
        self.last_user_message: str | None = None

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> AccountResult:
        del payload, context, pin_verified
        self.last_user_message = user_message
        return AccountResult(
            outcome=AccountOutcome.OK,
            response="*Your Balance*\n\n1. First Bank (****7890): **₦30,000.00**",
        )


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
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                route=InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.94,
                    detected_language="Yoruba",
                    target_intent="beneficiary",
                    reason="explicit beneficiary request",
                ),
            )
        },
        "recursion_limit": 50,
    }

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
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                route=InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.9,
                    detected_language="English",
                    target_intent=None,
                    reason="slot update",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload
    assert "stashed_sessions" not in updates


@pytest.mark.asyncio
async def test_interrupt_input_replaces_transfer_with_new_transfer_without_stash() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_3",
        phone_number="2348033333333",
        channel="whatsapp",
        last_message_text="send 8k to tolu",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t_old"], fields_by_task={"t_old": ["amount"]}),
        tasks={
            "t_old": TaskSpec(
                id="t_old",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mercy", "amount": 5000},
            )
        },
        waves=[["t_old"]],
        current_wave_index=0,
        session_stack=[
            ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW"),
            ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="CONFIRM"),
        ],
        active_domain="transfer",
    )
    planner_output = PlannerOutput(
        primary_intent="transfer",
        response="",
        confidence=0.96,
        detected_language="English",
        normalized_instruction="send 8k to tolu",
        tasks=[
            PlannedTask(
                task_id="t_new",
                action="send_money",
                executor="transfer",
                instruction="Send 8000 to Tolu",
                parameters=TaskParameters(amount=8000, recipient="Tolu"),
                risk="MONEY_MOVE",
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                route=InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.93,
                    detected_language="English",
                    target_intent="transfer",
                    reason="fresh transfer request",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert "stashed_sessions" not in updates
    assert updates["waves"] == [["t_new"]]
    assert list(updates["tasks"].keys()) == ["t_new"]
    assert updates["tasks"]["t_new"].type == "transfer"
    assert len(updates["session_stack"]) == 1
    assert updates["session_stack"][0].domain == "query"
    assert updates["active_domain"] == "query"


@pytest.mark.asyncio
async def test_interrupt_confirmation_replaces_transfer_with_airtime_without_stash() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_4",
        phone_number="2348044444444",
        channel="whatsapp",
        last_message_text="buy 2k airtime",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_old"]),
        tasks={
            "t_old": TaskSpec(
                id="t_old",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"confirmation": {"confirmed": False}, "recipient_name": "Grace", "amount": 5000},
            )
        },
        session_stack=[
            ActiveSession(domain="support", state="RUNNING", interrupt_policy="ALLOW"),
            ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="CONFIRM"),
        ],
        active_domain="transfer",
    )
    planner_output = PlannerOutput(
        primary_intent="airtime",
        response="",
        confidence=0.95,
        detected_language="English",
        normalized_instruction="buy 2k airtime",
        tasks=[
            PlannedTask(
                task_id="t_airtime",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy airtime",
                parameters=TaskParameters(amount=2000),
                risk="MONEY_MOVE",
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                route=InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.91,
                    detected_language="English",
                    target_intent="airtime",
                    reason="new airtime transaction",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert "stashed_sessions" not in updates
    assert updates["waves"] == [["t_airtime"]]
    assert updates["tasks"]["t_airtime"].type == "airtime"
    assert [s.domain for s in updates["session_stack"]] == ["support"]
    assert updates["active_domain"] == "support"


@pytest.mark.asyncio
async def test_interrupt_auth_replaces_transfer_with_data_without_stash() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_5",
        phone_number="2348055555555",
        channel="whatsapp",
        last_message_text="buy 1gb data",
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t_old"], auth_method="pin"),
        tasks={
            "t_old": TaskSpec(
                id="t_old",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_name": "Grace", "amount": 5000},
            )
        },
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_AUTH", interrupt_policy="BLOCK")],
        active_domain="transfer",
    )
    planner_output = PlannerOutput(
        primary_intent="data",
        response="",
        confidence=0.93,
        detected_language="English",
        normalized_instruction="buy 1gb data",
        tasks=[
            PlannedTask(
                task_id="t_data",
                action="buy_data",
                executor="data",
                instruction="Buy 1GB data",
                parameters=TaskParameters(plan="1GB"),
                risk="MONEY_MOVE",
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                route=InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.9,
                    detected_language="English",
                    target_intent="data",
                    reason="new data transaction",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert "stashed_sessions" not in updates
    assert updates["tasks"]["t_data"].type == "data"
    assert updates["waves"] == [["t_data"]]
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None


@pytest.mark.asyncio
async def test_confirmation_continue_flow_keeps_pending_and_reissues_confirmation_prompt() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_6",
        phone_number="2348066666666",
        channel="whatsapp",
        last_message_text="yes continue",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acc-1",
                    "bank_name": "First Bank",
                    "account_number": "1234567890",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-1",
                    "source_account_id": "acc-1",
                    "source_account_number": "1234567890",
                    "source_bank_name": "First Bank",
                    "confirmation": {
                        "summary": "Confirm transfer to Tolu",
                        "snapshot": {"amount": 5000, "sourceBank": "First Bank", "sourceAccount": "1234567890"},
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                PlannerOutput(primary_intent="transfer"),
                route=InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.71,
                    detected_language="English",
                    target_intent=None,
                    target_mode=None,
                    reason="still discussing same flow",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["outbox"][0]["type"] == "request_confirmation"
    assert "From: First Bank (···7890)" in updates["outbox"][0]["summary"]


@pytest.mark.asyncio
async def test_confirmation_reprompt_uses_interrupt_prompt_without_duplicate_source_line() -> None:
    summary_with_source = "Confirm transfer to Tolu\n\nFrom: First Bank (···7890)"
    state = OrchestratorState(
        user_id="u_interrupt_6b",
        phone_number="2348066666667",
        channel="whatsapp",
        last_message_text="continue",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt=summary_with_source),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-1b",
                    "confirmation": {
                        "summary": "Confirm transfer to Tolu",
                        "snapshot": {"amount": 5000},
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                PlannerOutput(primary_intent="transfer"),
                route=InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.72,
                    detected_language="English",
                    target_intent=None,
                    target_mode=None,
                    reason="still discussing same flow",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["outbox"][0]["type"] == "request_confirmation"
    assert updates["outbox"][0]["summary"].count("From: First Bank (···7890)") == 1


@pytest.mark.asyncio
async def test_confirmation_switch_to_account_is_direct_and_stashes_transfer() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_7",
        phone_number="2348077777777",
        channel="whatsapp",
        last_message_text="what's my balance",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"confirmation": {"summary": "Confirm transfer"}},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="CONFIRM")],
        active_domain="transfer",
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.96,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="new account request",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert len(updates["stashed_sessions"]) == 1
    task_ids = list(updates["tasks"].keys())
    assert len(task_ids) == 1
    assert updates["tasks"][task_ids[0]].type == "account"
    assert updates["tasks"][task_ids[0]].payload["message"] == "what's my balance"


@pytest.mark.asyncio
async def test_confirmation_switch_to_account_uses_user_message_for_balance_execution() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_7b",
        phone_number="2348077777778",
        channel="whatsapp",
        last_message_text="what's my balance",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        loaded_context={"language": "en", "accounts": [{"bank_name": "First Bank", "account_number": "1234567890", "mandate_status": "ready", "mandate_id": "m1"}]},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "confirmation": {
                        "summary": "Confirm transfer",
                        "snapshot": {"sourceBank": "First Bank", "sourceAccount": "1234567890"},
                    }
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="CONFIRM")],
        active_domain="transfer",
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.96,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="new account request",
        )
    )
    interrupt_config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, interrupt_config)
    switched_state = state.model_copy(update=updates)
    account_worker = _AccountBalanceWorker()
    execution_config: RunnableConfig = {
        "configurable": {"services": {"account": account_worker}},
        "recursion_limit": 50,
    }

    execution_updates = await advance_wave(switched_state, execution_config)

    assert account_worker.last_user_message == "what's my balance"
    assert execution_updates["outbox"][0]["text"].startswith("*Your Balance*")


@pytest.mark.asyncio
async def test_confirmation_reject_flow_cancels_task() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_8",
        phone_number="2348088888888",
        channel="whatsapp",
        last_message_text="no",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"confirmation": {"summary": "Confirm transfer"}},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="reject_flow",
            confidence=0.97,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="explicit rejection",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.CANCELLED


@pytest.mark.asyncio
async def test_auth_approve_flow_advances_non_pin_auth_to_executing() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_9",
        phone_number="2348099999999",
        channel="whatsapp",
        last_message_text="yes",
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="otp"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="approve_flow",
            confidence=0.92,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="explicit approval",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXECUTING


@pytest.mark.asyncio
async def test_callback_pin_verified_auto_approves_confirmation_without_router_call() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_10",
        phone_number="2348010101010",
        channel="whatsapp",
        last_message_text=None,
        last_callback={"pin_verified": True, "flow_type": "transfer"},
        pin_verified=True,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm transfer"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"confirmation": {"summary": "Confirm transfer", "confirmed": False}},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXECUTING
    assert updates["tasks"]["t1"].payload["confirmation"]["confirmed"] is True


@pytest.mark.asyncio
async def test_callback_pin_verified_auto_approves_auth_without_router_call() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_11",
        phone_number="2348010101011",
        channel="whatsapp",
        last_message_text=None,
        last_callback={"pin_verified": True, "flow_type": "transfer"},
        pin_verified=True,
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"confirmation": {"summary": "Confirm transfer"}},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXECUTING


@pytest.mark.asyncio
async def test_callback_flow_type_mismatch_reprompts_confirmation_without_advancing() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_12",
        phone_number="2348010101012",
        channel="whatsapp",
        last_message_text=None,
        last_callback={"pin_verified": True, "flow_type": "airtime"},
        pin_verified=True,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm transfer"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-mismatch",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 5000}},
                },
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["outbox"][0]["type"] == "request_confirmation"
    assert updates["outbox"][0]["summary"] == "Confirm transfer"


@pytest.mark.asyncio
async def test_text_abort_does_not_autoapprove_when_no_callback_payload() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_13",
        phone_number="2348010101013",
        channel="whatsapp",
        last_message_text="Abort",
        last_callback=None,
        pin_verified=True,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm transfer"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"confirmation": {"summary": "Confirm transfer", "confirmed": False}},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="reject_flow",
            confidence=0.95,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="explicit cancellation",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.CANCELLED
