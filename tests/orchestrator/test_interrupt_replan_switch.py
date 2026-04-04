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
from shared.i18n import render_cancelled_prompt
from shared.types.planner import (
    InterruptRouteDecision,
    PlannedTask,
    PlannerOutput,
    SemanticRouteDecision,
    TaskParameters,
)


class _MockPlanner:
    def __init__(
        self,
        output: PlannerOutput,
        route: InterruptRouteDecision | None = None,
        semantic_route: SemanticRouteDecision | None = None,
    ) -> None:
        self._output = output
        self._route = route
        self._semantic_route = semantic_route
        self.route_calls = 0

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        return self._output

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        self.route_calls += 1
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

    async def route_semantic_turn(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> SemanticRouteDecision:
        del phone_number, text, context, path_label
        if self._semantic_route is not None:
            return self._semantic_route
        target_intent = (self._route.target_intent if self._route else None) or "transfer"
        decision_map = {
            "transfer": "domain_transfer",
            "airtime": "domain_airtime",
            "data": "domain_data",
        }
        return SemanticRouteDecision(
            decision=decision_map.get(target_intent, "planner_ambiguous"),
            confidence=0.8,
            detected_language="English",
            target_intent=target_intent if target_intent in {"transfer", "airtime", "data"} else None,
            expected_transaction_executors=[target_intent] if target_intent in {"transfer", "airtime", "data"} else [],
            reason="mock semantic route",
        )


class _RouteOnlyPlanner:
    def __init__(self, route: InterruptRouteDecision, semantic_route: SemanticRouteDecision | None = None) -> None:
        self._route = route
        self._semantic_route = semantic_route
        self.route_calls = 0

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        self.route_calls += 1
        return self._route

    async def route_semantic_turn(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> SemanticRouteDecision:
        del phone_number, text, context, path_label
        if self._semantic_route is not None:
            return self._semantic_route
        return SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.0,
            detected_language=None,
            target_intent=None,
            expected_transaction_executors=[],
            reason="mock default",
        )

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
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

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        raise AssertionError("plan_tasks should not be called for callback auto-approve")

    async def route_semantic_turn(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> SemanticRouteDecision:
        del phone_number, text, context, path_label
        raise AssertionError("route_semantic_turn should not be called for callback auto-approve")


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
    switched_task_ids = list(updates["tasks"].keys())
    assert len(switched_task_ids) == 1
    assert updates["waves"] == [switched_task_ids]
    assert updates["tasks"][switched_task_ids[0]].type == "beneficiary"


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
async def test_interrupt_input_with_pending_beneficiary_clarification_blocks_intent_switch() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_beneficiary_lock",
        phone_number="2348022222299",
        channel="whatsapp",
        last_message_text="what is my balance",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["beneficiary_id"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.RESOLVED,
                payload={"recipient_name": "Tolu", "amount": 5000, "idempotency_key": "old-key"},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _RouteOnlyPlanner(
                InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.94,
                    detected_language="English",
                    target_intent="account",
                    target_mode="new",
                    reason="explicit account request",
                )
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].task_ids == ["t1"]
    assert updates["tasks"]["t1"].type == "transfer"
    assert updates["tasks"]["t1"].stage == TaskStage.RESOLVED
    assert updates["tasks"]["t1"].payload["idempotency_key"] == "old-key"
    assert "stashed_sessions" not in updates


@pytest.mark.asyncio
async def test_interrupt_input_stashes_transfer_and_routes_new_single_transfer_directly() -> None:
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
    assert len(updates["stashed_sessions"]) == 1
    switched_task_ids = list(updates["tasks"].keys())
    assert len(switched_task_ids) == 1
    assert updates["waves"] == [switched_task_ids]
    assert updates["tasks"][switched_task_ids[0]].type == "transfer"
    assert updates["tasks"][switched_task_ids[0]].payload["message"] == "send 8k to tolu"
    assert "skip_extraction" not in updates["tasks"][switched_task_ids[0]].payload
    assert len(updates["session_stack"]) == 1
    assert updates["session_stack"][0].domain == "query"
    assert updates["active_domain"] == "query"


@pytest.mark.asyncio
async def test_interrupt_confirmation_stashes_transfer_and_routes_new_airtime_directly() -> None:
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
    assert len(updates["stashed_sessions"]) == 1
    switched_task_ids = list(updates["tasks"].keys())
    assert len(switched_task_ids) == 1
    assert updates["waves"] == [switched_task_ids]
    assert updates["tasks"][switched_task_ids[0]].type == "airtime"
    assert updates["tasks"][switched_task_ids[0]].payload["message"] == "buy 2k airtime"
    assert "skip_extraction" not in updates["tasks"][switched_task_ids[0]].payload
    assert [s.domain for s in updates["session_stack"]] == ["support"]
    assert updates["active_domain"] == "support"


@pytest.mark.asyncio
async def test_interrupt_auth_stashes_transfer_and_routes_new_data_directly() -> None:
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
    assert len(updates["stashed_sessions"]) == 1
    switched_task_ids = list(updates["tasks"].keys())
    assert len(switched_task_ids) == 1
    assert updates["tasks"][switched_task_ids[0]].type == "data"
    assert updates["tasks"][switched_task_ids[0]].payload["message"] == "buy 1gb data"
    assert "skip_extraction" not in updates["tasks"][switched_task_ids[0]].payload
    assert updates["waves"] == [switched_task_ids]
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None


@pytest.mark.asyncio
async def test_interrupt_input_stashes_transfer_and_hands_multi_transfer_replacement_to_planner() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_5b",
        phone_number="2348055555566",
        channel="whatsapp",
        last_message_text="send 10k to mum and 5k to gaines",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t_old"], fields_by_task={"t_old": ["amount"]}),
        tasks={
            "t_old": TaskSpec(
                id="t_old",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Grace", "amount": 5000},
            )
        },
        waves=[["t_old"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="CONFIRM")],
        active_domain="transfer",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _RouteOnlyPlanner(
                InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.92,
                    detected_language="English",
                    target_intent="transfer",
                    reason="fresh transfer replacement",
                ),
                semantic_route=SemanticRouteDecision(
                    decision="planner_mixed",
                    confidence=0.9,
                    detected_language="English",
                    target_intent="transfer",
                    expected_transaction_executors=["transfer"],
                    reason="multi transfer needs decomposition",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert len(updates["stashed_sessions"]) == 1
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None


@pytest.mark.asyncio
async def test_confirmation_continue_flow_resets_task_to_extracted() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_6",
        phone_number="2348066666666",
        channel="whatsapp",
        last_message_text="change to 20k",
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
                    confidence=0.85,
                    detected_language="English",
                    target_intent=None,
                    target_mode=None,
                    reason="slot update",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}
    assert updates["tasks"]["t1"].payload["previous_confirmation_snapshot"] == {
        "amount": 5000,
        "sourceBank": "First Bank",
        "sourceAccount": "1234567890",
    }
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_confirmation_amount_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_shortcut_1",
        phone_number="2348066666677",
        channel="whatsapp",
        last_message_text="make it 20k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-1",
                    "confirmation": {
                        "summary": "Confirm transfer to Mum",
                        "snapshot": {"amount": 10000, "recipient_name": "Mum"},
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FailIfRouterCalledPlanner(),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}
    assert updates["tasks"]["t1"].payload["previous_confirmation_snapshot"] == {
        "amount": 10000,
        "recipient_name": "Mum",
    }


@pytest.mark.asyncio
async def test_confirmation_exact_repeat_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_repeat_1",
        phone_number="2348066666678",
        channel="whatsapp",
        last_message_text="send 10k to mum",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "message": "send 10k to mum",
                    "instruction": "send 10k to mum",
                    "idempotency_key": "idem-1",
                    "confirmation": {
                        "summary": "Confirm transfer to Mum",
                        "snapshot": {"amount": 10000, "recipient_name": "Mum"},
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FailIfRouterCalledPlanner(),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_input_numeric_source_selection_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_shortcut_input_1",
        phone_number="2348066666699",
        channel="whatsapp",
        last_message_text="1",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["source_account_id"]},
            prompt="Which account should I use?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "confirmation": {
                        "summary": "Confirm transfer to Mum",
                        "snapshot": {"amount": 30000, "recipient_name": "Mum"},
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FailIfRouterCalledPlanner(),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}


@pytest.mark.asyncio
async def test_input_account_entry_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_shortcut_input_2",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="8162511023",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_account"]},
            prompt="What account number should I use?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FailIfRouterCalledPlanner(),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_input_recipient_alias_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_shortcut_input_3",
        phone_number="2348066666701",
        channel="whatsapp",
        last_message_text="Its to mum",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_account", "recipient_bank_name"]},
            prompt="What's recipient's account number and bank?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 10000.0},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FailIfRouterCalledPlanner(),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_input_query_pivot_still_uses_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_shortcut_input_4",
        phone_number="2348066666702",
        channel="whatsapp",
        last_message_text="How much did I spend last week?",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_account", "recipient_bank_name"]},
            prompt="What's recipient's account number and bank?",
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 10000.0},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.96,
            detected_language="English",
            target_intent="query",
            target_mode="new",
            reason="explicit query pivot",
        )
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert updates["pending_interrupt"] is None
    assert len(updates["stashed_sessions"]) == 1
    switched_task_ids = list(updates["tasks"].keys())
    assert len(switched_task_ids) == 1
    assert updates["tasks"][switched_task_ids[0]].type == "query"


@pytest.mark.asyncio
async def test_confirmation_ambiguous_message_still_falls_back_to_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_router_fallback_1",
        phone_number="2348066666701",
        channel="whatsapp",
        last_message_text="hello, maybe later but what do you think",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "message": "send 10k to mum",
                    "instruction": "send 10k to mum",
                    "confirmation": {"summary": "Confirm transfer to Mum"},
                },
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.7,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="router fallback",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED


def _build_multi_transfer_confirmation_state(message_text: str) -> OrchestratorState:
    return OrchestratorState(
        user_id="u_interrupt_multi_confirm",
        phone_number="2348066666777",
        channel="whatsapp",
        last_message_text=message_text,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_mum", "t_gaines"]),
        tasks={
            "t_mum": TaskSpec(
                id="t_mum",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Mum",
                    "idempotency_key": "idem-mum",
                    "confirmation": {"summary": "Confirm Mum", "snapshot": {"amount": 10000, "recipient_name": "Mum"}},
                },
            ),
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Gaines",
                    "idempotency_key": "idem-gaines",
                    "confirmation": {
                        "summary": "Confirm Gaines",
                        "snapshot": {"amount": 5000, "recipient_name": "Gaines"},
                    },
                },
            ),
        },
    )


@pytest.mark.asyncio
async def test_confirmation_continue_flow_targets_only_matching_recipient_task() -> None:
    state = _build_multi_transfer_confirmation_state("change amount for gaines to 10k too")
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                PlannerOutput(primary_intent="transfer"),
                route=InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.9,
                    detected_language="English",
                    target_intent=None,
                    target_mode=None,
                    reason="recipient-scoped update",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_mum"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_mum"].payload["confirmation"]["summary"] == "Confirm Mum"
    assert updates["tasks"]["t_mum"].payload["idempotency_key"] == "idem-mum"
    assert updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_gaines"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t_gaines"].payload
    assert updates["last_interrupt"].task_ids == ["t_gaines"]


@pytest.mark.asyncio
async def test_confirmation_continue_flow_ambiguous_update_resets_all_tasks() -> None:
    state = _build_multi_transfer_confirmation_state("change amount to 10k")
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                PlannerOutput(primary_intent="transfer"),
                route=InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.9,
                    detected_language="English",
                    target_intent=None,
                    target_mode=None,
                    reason="ambiguous update",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_mum"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_mum"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t_mum"].payload
    assert updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_gaines"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t_gaines"].payload
    assert set(updates["last_interrupt"].task_ids) == {"t_mum", "t_gaines"}


@pytest.mark.asyncio
async def test_confirmation_continue_flow_collective_update_resets_all_tasks() -> None:
    state = _build_multi_transfer_confirmation_state("add narration for both as monthly allowance")
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                PlannerOutput(primary_intent="transfer"),
                route=InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.9,
                    detected_language="English",
                    target_intent=None,
                    target_mode=None,
                    reason="collective update",
                ),
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_mum"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_mum"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t_mum"].payload
    assert updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_gaines"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t_gaines"].payload
    assert set(updates["last_interrupt"].task_ids) == {"t_mum", "t_gaines"}


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
        loaded_context={
            "language": "en",
            "accounts": [
                {
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
async def test_confirmation_same_flow_switch_intent_shortcuts_back_to_continue_flow() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_same_flow_1",
        phone_number="2348077777700",
        channel="whatsapp",
        last_message_text="change it to 20k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Mum",
                    "confirmation": {"summary": "Confirm transfer"},
                    "idempotency_key": "idem-same-flow-1",
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.88,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="same flow correction misclassified as switch",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_confirmation_update_targets_only_airtime_task_in_mixed_batch() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_7c",
        phone_number="2348077777779",
        channel="whatsapp",
        last_message_text="Make the airtime 2k instead",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Yusuf Ibrahim",
                    "recipient_account": "0760505262",
                    "confirmation": {
                        "summary": "Confirm transfer",
                        "snapshot": {
                            "amount": 10000,
                            "recipient_name": "Gaines",
                            "recipient_account": "0760505262",
                            "recipient_bank_name": "Access Bank",
                        },
                    },
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                    "confirmation": {
                        "summary": "Confirm airtime",
                        "snapshot": {
                            "amount": 1000,
                            "recipient_phone": "08162511023",
                            "network": "MTN",
                        },
                    },
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.96,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="confirmation correction",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_transfer"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_transfer"].payload["idempotency_key"] == "idem-transfer"
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_airtime"].payload["confirmation"] == {}
    assert "idempotency_key" not in updates["tasks"]["t_airtime"].payload
    assert updates["last_interrupt"].task_ids == ["t_airtime"]


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
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["final_response"] == render_cancelled_prompt("en")


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
    assert updates["outbox"][0]["actionable_payload"]["idempotency_key"] == "idem-mismatch"
    assert updates["outbox"][0]["summary"] == "Confirm transfer"


@pytest.mark.asyncio
async def test_input_obvious_cancel_shortcut_resets_for_fresh_start() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_cancel_all",
        phone_number="2348010101099",
        channel="whatsapp",
        last_message_text="stop this transfer",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t_transfer"], fields_by_task={"t_transfer": ["amount"]}),
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 10000},
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.DRAFT,
                payload={"amount": 5000},
            ),
            "t_account": TaskSpec(
                id="t_account",
                type="account",
                stage=TaskStage.EXTRACTED,
                payload={"action": "check_balance"},
            ),
        },
        waves=[["t_transfer", "t_airtime", "t_account"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner(), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert updates["stashed_query_session"] is None
    assert updates["stashed_sessions"] == []
    assert updates["final_response"] == render_cancelled_prompt("en")


@pytest.mark.asyncio
async def test_confirmation_obvious_cancel_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_cancel_confirmation",
        phone_number="2348010101100",
        channel="whatsapp",
        last_message_text="please cancel",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm transfer"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"recipient_name": "Mum", "amount": 10000},
            )
        },
        waves=[["t1"]],
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner(), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["final_response"] == render_cancelled_prompt("en")


@pytest.mark.asyncio
async def test_confirmation_ambiguous_cancel_phrase_falls_back_to_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_cancel_confirmation_ambiguous",
        phone_number="2348010101101",
        channel="whatsapp",
        last_message_text="maybe cancel later",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm transfer"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"recipient_name": "Mum", "amount": 10000},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="cancel",
            confidence=0.9,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="ambiguous cancellation",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["final_response"] == render_cancelled_prompt("en")


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
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["final_response"] == render_cancelled_prompt("en")


@pytest.mark.asyncio
async def test_status_query_recap_preserves_pending_interrupt_without_task_reset() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_1",
        phone_number="2348010101091",
        channel="whatsapp",
        last_message_text="where did we stop",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["beneficiary_id"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.RESOLVED,
                payload={"recipient_name": "Tolu", "amount": 5000, "idempotency_key": "idem-status-1"},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="status_query",
            status_query_type="recap",
            confidence=0.91,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="flow recap request",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["pending_interrupt"].task_ids == ["t1"]
    assert updates["tasks"]["t1"].stage == TaskStage.RESOLVED
    assert updates["tasks"]["t1"].payload["idempotency_key"] == "idem-status-1"
    assert updates["outbox"][0]["type"] == "say"
    assert "transfer flow" in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_status_query_requirements_preserves_pending_interrupt() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_2",
        phone_number="2348010101092",
        channel="whatsapp",
        last_message_text="what do you need from me",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["beneficiary_id"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu"},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="status_query",
            status_query_type="requirements",
            confidence=0.95,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="requirements request",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["outbox"][0]["type"] == "say"
    assert "I still need: beneficiary selection." in updates["outbox"][0]["text"]
    assert "replying with the number" in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_status_query_multilingual_route_keeps_interrupt_active() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_3",
        phone_number="2348010101093",
        channel="whatsapp",
        last_message_text="kini mo tun fi ranse",
        pending_interrupt=PendingInterrupt(
            kind="confirmation",
            task_ids=["t1"],
            prompt="Confirm transfer to Tolu",
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"confirmation": {"summary": "Confirm transfer to Tolu"}},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="status_query",
            status_query_type="requirements",
            confidence=0.88,
            detected_language="Yoruba",
            target_intent=None,
            target_mode=None,
            reason="yoruba status query",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["pending_interrupt"].kind == "confirmation"
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["outbox"][0]["type"] == "say"


@pytest.mark.asyncio
async def test_status_query_without_transaction_flow_recovers_to_fresh_query_route() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_query_recover_1",
        phone_number="23480101010935",
        channel="whatsapp",
        last_message_text="What's my income this month",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["time_period"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="query",
                stage=TaskStage.EXTRACTED,
                payload={"message": "Show me my credit transactions"},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="status_query",
            status_query_type="recap",
            confidence=0.88,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="misclassified fresh analytics ask",
        ),
        semantic_route=SemanticRouteDecision(
            decision="domain_query",
            confidence=0.94,
            detected_language="English",
            mode="new",
            target_intent="query",
            reason="fresh income analytics query",
        ),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["last_interrupt"] is None
    assert set(updates["tasks"].keys()) == {"interrupt_query_1"}
    task = updates["tasks"]["interrupt_query_1"]
    assert task.type == "query"
    assert task.payload["message"] == "What's my income this month"
    assert task.payload["force_new_query"] is True
    assert updates["waves"] == [["interrupt_query_1"]]


@pytest.mark.asyncio
async def test_transfer_input_unclear_reprompt_uses_short_account_bank_reminder() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_reprompt_1",
        phone_number="2348010101094",
        channel="whatsapp",
        last_message_text="hi",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_account", "recipient_bank_name"]},
            prompt="I found Mum (MERCY JOHNSON).\n\nWhat's Tolu's account number and bank?",
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu", "recipient_resolved_name": "TOLU ADEDAYO"},
            )
        },
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="unclear",
            confidence=0.55,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="smalltalk while waiting for transfer input",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["outbox"][0]["type"] == "say"
    assert updates["outbox"][0]["text"] == "What's Tolu (TOLU ADEDAYO)'s account number and bank?"
    assert "I found" not in updates["outbox"][0]["text"]
