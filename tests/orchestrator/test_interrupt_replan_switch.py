"""Interrupt-node tests for multilingual stash-and-switch behavior."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    AccountResult,
    ActiveSession,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from shared.i18n.bridge import render_cancelled_prompt
from shared.i18n.renderer import render_message
from shared.types.planner import (
    InterruptRouteDecision,
    PendingActionEditDecision,
    PendingActionFieldUpdates,
    PendingActionTargetedUpdate,
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

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None, path_label: str = "planner_path") -> PlannerOutput:
        del phone_number, text, context
        return self._output

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        del phone_number, text, context, path_label, prompt_mode
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

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return PendingActionEditDecision(operation="unclear", confidence=0.0, reason="not an edit")

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
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        del phone_number, text, context, path_label, prompt_mode
        self.route_calls += 1
        return self._route

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return PendingActionEditDecision(operation="unclear", confidence=0.0, reason="not an edit")

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

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None, path_label: str = "planner_path") -> PlannerOutput:
        del phone_number, text, context
        raise AssertionError("plan_tasks should not be called for direct switch targets")


class _FailIfRouterCalledPlanner:
    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        del phone_number, text, context, path_label, prompt_mode
        raise AssertionError("route_pending_input should not be called for callback auto-approve")

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return PendingActionEditDecision(operation="unclear", confidence=0.0, reason="not an edit")

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None, path_label: str = "planner_path") -> PlannerOutput:
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


class _ScheduleReadInterruptPlanner(_FailIfRouterCalledPlanner):
    def __init__(self, route: SemanticRouteDecision) -> None:
        self._route = route
        self.schedule_read_calls = 0

    async def route_schedule_read_turn(
        self,
        phone_number: str,
        text: str,
        *,
        path_label: str = "interrupt_path",
    ) -> SemanticRouteDecision:
        del phone_number, text, path_label
        self.schedule_read_calls += 1
        return self._route


class _PendingEditOnlyPlanner(_FailIfRouterCalledPlanner):
    def __init__(self, decision: PendingActionEditDecision) -> None:
        self._decision = decision

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return self._decision


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


class _TransferConfirmationRenderWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        amount = float(payload.get("amount") or 0.0)
        recipient_name = str(payload.get("recipient_name") or "").strip()
        resolved_name = str(payload.get("recipient_resolved_name") or recipient_name).strip()
        bank_name = str(payload.get("recipient_bank_name") or "").strip()
        account_number = str(payload.get("recipient_account") or "").strip()
        narration = str(payload.get("narration") or "").strip()
        lines = [f"₦{amount:,.0f} → {recipient_name} ({resolved_name})", f"{bank_name} • {account_number}"]
        if narration:
            lines.append(f"Narration: {narration.title()}")
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="\n".join(lines),
            confirmation_snapshot={
                "amount": amount,
                "recipient_name": recipient_name,
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


@pytest.mark.asyncio
async def test_pending_schedule_confirmation_allows_read_only_schedule_view() -> None:
    planner = _ScheduleReadInterruptPlanner(
        SemanticRouteDecision(
            decision="domain_schedule",
            mode="new",
            target_intent="schedule",
            confidence=0.93,
            detected_language="English",
            expected_transaction_executors=[],
            schedule_response_mode="list",
            reason="show scheduled transactions",
        )
    )
    frame = ContextFrame(
        frame_id="schedule_list_existing",
        frame_type=ContextFrameType.SCHEDULE_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.GENERIC,
                entity_id="sch-1",
                label="Transfer: ₦20,000 FATIMA ZAHRA MUSA • One Time at 2:00 PM Lagos time",
                data={
                    "type": "scheduled_transaction",
                    "schedule_id": "sch-1",
                    "domain": "Transfer",
                    "amount": "₦20,000",
                    "target": "FATIMA ZAHRA MUSA",
                    "recurrence": "One Time",
                    "schedule_time": "2:00 PM Lagos time",
                    "next_run": "May 23, 2026 at 2:00 PM Lagos time",
                    "source_bank_name": "Access Bank",
                    "status": "active",
                },
            )
        ],
        created_at_ts=9_999_999_999,
    )
    interrupt = PendingInterrupt(kind="confirmation", task_ids=["t_schedule"], prompt="Confirm Schedule Update")
    state = OrchestratorState(
        user_id="u_pending_schedule_read",
        phone_number="2348011111199",
        channel="whatsapp",
        last_message_text="show my scheduled transaction",
        pending_interrupt=interrupt,
        tasks={
            "t_schedule": TaskSpec(
                id="t_schedule",
                type="schedule",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "edit_scheduled_transaction",
                    "confirmation": {
                        "summary": "Transfer: ₦20,000 FATIMA ZAHRA MUSA • One Time at 9:00 AM Lagos time"
                    },
                },
            )
        },
        context_frames=[frame],
    )

    updates = await handle_pending_interrupt(
        state,
        {"configurable": {"task_planner": planner}},
    )

    assert planner.schedule_read_calls == 1
    assert updates["pending_interrupt"] == interrupt
    assert updates["semantic_path_shape"] == "interrupt_schedule_read_context"
    assert updates["routing_target_domain"] == "schedule"
    response = updates["outbox"][0]["text"]
    assert "Scheduled Transaction Details" in response
    assert "2:00 PM Lagos time" in response
    assert "Confirm Schedule Update" not in response


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
async def test_interrupt_input_with_beneficiary_bank_label_continues_flow_without_router() -> None:
    second_id = "bene-gtb"
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.94,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="should not be called",
        )
    )
    state = OrchestratorState(
        user_id="u_interrupt_beneficiary_bank_label",
        phone_number="2348022222299",
        channel="telegram",
        last_message_text="the GTB",
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
                payload={
                    "recipient_name": "Tolu",
                    "amount": 10000,
                    "idempotency_key": "old-key",
                    "beneficiary_candidates": [
                        {"index": 1, "beneficiary_id": "bene-access", "label": "Tolu Adebayo • Access Bank • ****0001"},
                        {"index": 2, "beneficiary_id": second_id, "label": "Tolu Adeyemi • GTBank • ****0002"},
                    ],
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_interrupt_input_bank_only_reply_continues_transfer_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_bank_only_slot",
        phone_number="2348022222301",
        channel="whatsapp",
        last_message_text="GTB",
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
                payload={
                    "recipient_name": "Tolu",
                    "recipient_account": "2010000002",
                    "idempotency_key": "old-key",
                },
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_interrupt_input_self_line_reply_continues_data_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_self_line_slot",
        phone_number="2348022222302",
        channel="whatsapp",
        last_message_text="my line",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["target_phone"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="data",
                stage=TaskStage.RESOLVED,
                payload={"network": "MTN", "idempotency_key": "old-key"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_interrupt_input_network_reply_continues_data_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_network_slot",
        phone_number="2348022222303",
        channel="whatsapp",
        last_message_text="MTN",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["network"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="data",
                stage=TaskStage.RESOLVED,
                payload={"target_phone": "08162511023", "idempotency_key": "old-key"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_interrupt_input_self_line_reply_continues_airtime_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_self_line_slot",
        phone_number="2348022222304",
        channel="whatsapp",
        last_message_text="my line",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_phone"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.RESOLVED,
                payload={"amount": 1000, "network": "MTN", "idempotency_key": "old-key"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_interrupt_input_network_reply_continues_airtime_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_network_slot",
        phone_number="2348022222305",
        channel="whatsapp",
        last_message_text="MTN",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["network"]},
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.RESOLVED,
                payload={"amount": 1000, "recipient_phone": "08162511023", "idempotency_key": "old-key"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


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
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    amount=20000,
                    reason="scoped multi-transfer edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["amount"] == 20000
    assert updates["tasks"]["t1"].payload["confirmation"] == {"confirmed": False}
    assert updates["tasks"]["t1"].payload["previous_confirmation_snapshot"] == {
        "amount": 10000,
        "recipient_name": "Mum",
    }


@pytest.mark.asyncio
async def test_confirmation_data_amount_edit_clears_catalog_plan_before_reconfirming() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_amount_edit",
        phone_number="2348066666681",
        channel="whatsapp",
        last_message_text="make it 2k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-data",
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL099",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "plan_tags": ["monthly"],
                    "data_plan_candidates": [{"index": 1, "plan_code": "MD501"}],
                    "catalog_cache_stale": True,
                    "confirmation": {
                        "summary": "Confirm data purchase",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "target_phone": "08162511023",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["data"],
                    amount=2000,
                    reason="data amount edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_data"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED
    assert payload["amount"] == 2000
    assert payload["confirmation"] == {"confirmed": False}
    assert payload["plan_code"] is None
    assert payload["plan_name"] is None
    assert payload["biller_code"] is None
    assert payload["plan_size_gb"] is None
    assert payload["plan_validity_days"] is None
    assert payload["plan_tags"] == []
    assert payload["data_plan_candidates"] == []
    assert payload["catalog_cache_stale"] is False
    assert payload["size_preference"] is None
    assert payload["previous_confirmation_snapshot"]["plan_code"] == "MD501"
    assert "idempotency_key" not in payload


@pytest.mark.asyncio
async def test_confirmation_data_network_edit_clears_catalog_plan_before_reconfirming() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_network_edit",
        phone_number="2348066666682",
        channel="whatsapp",
        last_message_text="change it to airtel",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "recipient_name": "Mum",
                    "beneficiary_id": "ben-data-mtn",
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL099",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "plan_tags": ["monthly"],
                    "data_plan_candidates": [{"index": 1, "plan_code": "MD501"}],
                    "confirmation": {
                        "summary": "Confirm data purchase",
                        "snapshot": {
                            "network": "MTN",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["data"],
                    network="AIRTEL",
                    reason="data network edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_data"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED
    assert payload["network"] == "AIRTEL"
    assert payload["target_phone"] is None
    assert payload["phone"] is None
    assert payload["recipient_name"] is None
    assert payload["beneficiary_id"] is None
    assert payload["is_self"] is False
    assert payload["confirmation"] == {"confirmed": False}
    assert payload["plan_code"] is None
    assert payload["plan_name"] is None
    assert payload["biller_code"] is None
    assert payload["plan_size_gb"] is None
    assert payload["plan_validity_days"] is None
    assert payload["plan_tags"] == []
    assert payload["data_plan_candidates"] == []
    assert payload["previous_confirmation_snapshot"]["plan_code"] == "MD501"


@pytest.mark.asyncio
async def test_confirmation_airtime_phone_edit_infers_new_network_before_reconfirming() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_phone_network_edit",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="buy it for 08081234567",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_airtime"]),
        loaded_context={"language": "en"},
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-airtime",
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "recipient_name": "Mum",
                    "beneficiary_id": "ben-airtime-mtn",
                    "network": "MTN",
                    "is_self": True,
                    "confirmation": {
                        "summary": "Confirm airtime",
                        "snapshot": {
                            "amount": 1000,
                            "recipient_phone": "08162511023",
                            "network": "MTN",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["airtime"],
                    phone="08081234567",
                    reason="airtime phone edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_airtime"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED
    assert payload["recipient_phone"] == "08081234567"
    assert payload["phone"] == "08081234567"
    assert payload["recipient_name"] is None
    assert payload["beneficiary_id"] is None
    assert payload["is_self"] is False
    assert payload["network"] == "AIRTEL"
    assert payload["confirmation"] == {"confirmed": False}
    assert payload["previous_confirmation_snapshot"] == {
        "amount": 1000,
        "recipient_phone": "08162511023",
        "network": "MTN",
    }
    assert "idempotency_key" not in payload


@pytest.mark.asyncio
async def test_confirmation_airtime_network_edit_clears_mismatched_self_line() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_network_mismatch_edit",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="change airtime to Airtel",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_airtime"]),
        loaded_context={"language": "en"},
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "idempotency_key": "idem-airtime",
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "phone": "08162511023",
                    "recipient_name": "Mum",
                    "beneficiary_id": "ben-airtime-mtn",
                    "network": "MTN",
                    "is_self": True,
                    "confirmation": {
                        "summary": "Confirm airtime",
                        "snapshot": {
                            "amount": 1000,
                            "recipient_phone": "08162511023",
                            "network": "MTN",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["airtime"],
                    network="Airtel",
                    reason="airtime network edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_airtime"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED
    assert payload["network"] == "Airtel"
    assert payload["recipient_phone"] is None
    assert payload["phone"] is None
    assert payload["recipient_name"] is None
    assert payload["beneficiary_id"] is None
    assert payload["is_self"] is False
    assert payload["confirmation"] == {"confirmed": False}
    assert payload["previous_confirmation_snapshot"] == {
        "amount": 1000,
        "recipient_phone": "08162511023",
        "network": "MTN",
    }
    assert "idempotency_key" not in payload


@pytest.mark.asyncio
async def test_confirmation_data_phone_edit_to_different_network_clears_catalog_plan() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_phone_network_edit",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="buy it for 08081234567",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "recipient_name": "Mum",
                    "beneficiary_id": "ben-data-mtn",
                    "is_self": True,
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL099",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "plan_tags": ["monthly"],
                    "confirmation": {
                        "summary": "Confirm data purchase",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "target_phone": "08162511023",
                            "plan_code": "MD501",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["data"],
                    phone="08081234567",
                    reason="data target phone edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_data"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED
    assert payload["target_phone"] == "08081234567"
    assert payload["phone"] == "08081234567"
    assert payload["recipient_name"] is None
    assert payload["beneficiary_id"] is None
    assert payload["is_self"] is False
    assert payload["network"] == "AIRTEL"
    assert payload["confirmation"] == {"confirmed": False}
    assert payload["plan_code"] is None
    assert payload["plan_name"] is None
    assert payload["biller_code"] is None
    assert payload["plan_size_gb"] is None
    assert payload["plan_validity_days"] is None
    assert payload["plan_tags"] == []
    assert payload["data_plan_candidates"] == []
    assert payload["previous_confirmation_snapshot"]["plan_code"] == "MD501"


@pytest.mark.asyncio
async def test_confirmation_data_size_edit_clears_catalog_plan_before_reconfirming() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_size_edit",
        phone_number="2348066666683",
        channel="whatsapp",
        last_message_text="make it 5GB",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "plan_code": "MD108",
                    "plan_name": "MTN 3.5 GB",
                    "biller_code": "BIL099",
                    "plan_size_gb": 3.5,
                    "plan_validity_days": 30,
                    "plan_tags": ["monthly"],
                    "confirmation": {
                        "summary": "Confirm data purchase",
                        "snapshot": {
                            "network": "MTN",
                            "plan_code": "MD108",
                            "plan_name": "MTN 3.5 GB",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["data"],
                    size_preference="5GB",
                    reason="data size edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_data"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED
    assert payload["size_preference"] == "5GB"
    assert payload["confirmation"] == {"confirmed": False}
    assert payload["plan_code"] is None
    assert payload["plan_name"] is None
    assert payload["plan_size_gb"] is None
    assert payload["plan_validity_days"] is None
    assert payload["previous_confirmation_snapshot"]["plan_code"] == "MD108"


@pytest.mark.asyncio
async def test_confirmation_data_show_options_sets_catalog_alternative_state() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_show_options",
        phone_number="2348066666684",
        channel="whatsapp",
        last_message_text="what other plan within that range",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL099",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "plan_tags": ["monthly"],
                    "confirmation": {
                        "summary": "Confirm data purchase",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                        },
                    },
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="show_options",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["data"],
                    show_options=True,
                    reason="data plan alternatives",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    payload = updates["tasks"]["t_data"].payload

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED
    assert payload["show_plan_options"] is True
    assert payload["data_plan_exclude_codes"] == ["MD501"]
    assert payload["plan_code"] is None
    assert payload["plan_name"] is None
    assert payload["confirmation"] == {"confirmed": False}


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
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    reason="scoped multi-transfer edit",
                )
            ),
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
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    reason="scoped multi-transfer edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}


@pytest.mark.asyncio
async def test_input_source_account_reference_shortcut_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_shortcut_input_source_ref",
        phone_number="2348066666699",
        channel="telegram",
        last_message_text="my GTB",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["source_account_id"]},
            prompt="Which account should I use?",
        ),
        loaded_context={
            "language": "en",
            "accounts": [
                {"id": "acc-access", "bank_name": "Access Bank", "account_number": "2010000001"},
                {"id": "acc-gtb", "bank_name": "GTBank", "account_number": "2010000002"},
            ],
        },
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
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    reason="scoped multi-transfer edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["confirmation"] == {}


@pytest.mark.asyncio
async def test_data_plan_numeric_selection_shortcut_skips_interrupt_router() -> None:
    prompt = (
        "Which MTN data plan should I use?\n"
        "1. MTN 5 GB data bundle — ₦3,500 (30 days)\n"
        "2. MTN 3.5 GB — ₦2,000 (30 days)\n"
        "3. MTN 1.5 GB — ₦1,000 (30 days)\n"
        "Reply with 1, 2, or 3."
    )
    state = OrchestratorState(
        user_id="u_interrupt_data_plan_selection",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="2",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_data"],
            fields_by_task={"t_data": ["data_plan_id"]},
            prompt=prompt,
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "data_plan_candidates": [
                        {"index": 1, "plan_code": "MD501"},
                        {"index": 2, "plan_code": "MD350"},
                        {"index": 3, "plan_code": "MD150"},
                    ],
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_data_plan_input_greeting_nudges_without_repeating_full_plan_prompt() -> None:
    prompt = (
        "Which MTN data plan should I use?\n"
        "1. MTN 5 GB data bundle — ₦3,500 (30 days)\n"
        "2. MTN 3.5 GB — ₦2,000 (30 days)\n"
        "3. MTN 1.5 GB — ₦1,000 (30 days)\n"
        "Reply with 1, 2, or 3."
    )
    state = OrchestratorState(
        user_id="u_interrupt_data_plan_greeting",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="Hi",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_data"],
            fields_by_task={"t_data": ["data_plan_id"]},
            prompt=prompt,
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "data_plan_candidates": [
                        {"index": 1, "plan_code": "MD501"},
                        {"index": 2, "plan_code": "MD350"},
                        {"index": 3, "plan_code": "MD150"},
                    ],
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    response = updates["outbox"][0]["text"]

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].attempts == 1
    assert response == render_message(
        "orchestrator.execution.input_greeting_data_plan_network",
        "en",
        {"network": "MTN"},
    )
    assert "MTN 5 GB data bundle" not in response
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_data_preference_input_greeting_nudges_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_preference_greeting",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="Hi",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_data"],
            fields_by_task={"t_data": ["data_plan_preference"]},
            prompt="Sure. I'll use your MTN line. What budget or data size should I use?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"network": "MTN", "target_phone": "08162511023", "is_self": True},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    response = updates["outbox"][0]["text"]

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].attempts == 1
    assert response == render_message(
        "orchestrator.execution.input_greeting_data_preference_network",
        "en",
        {"network": "MTN"},
    )
    assert "What budget or data size should I use?" not in response
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_data_preference_reply_in_multi_slot_prompt_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_preference_multi_slot",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="4k",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_data"],
            fields_by_task={"t_data": ["target_phone", "data_plan_preference"]},
            prompt="Sure. Which Airtel line should I buy for, and what budget or data size should I use?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"network": "AIRTEL"},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_data_phone_reply_in_multi_slot_prompt_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_data_phone_multi_slot",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="08081234567",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_data"],
            fields_by_task={"t_data": ["target_phone", "data_plan_preference"]},
            prompt="Sure. Which Airtel line should I buy for, and what budget or data size should I use?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"network": "AIRTEL"},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_data"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_airtime_amount_input_greeting_nudges_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_amount_greeting",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="Hi",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_airtime"],
            fields_by_task={"t_airtime": ["amount"]},
            prompt="Sure. I'll use your MTN line. How much airtime should I buy?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"network": "MTN", "recipient_phone": "08162511023", "is_self": True},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    response = updates["outbox"][0]["text"]

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].attempts == 1
    assert response == render_message(
        "orchestrator.execution.input_greeting_airtime_amount_network",
        "en",
        {"network": "MTN"},
    )
    assert "How much airtime should I buy?" not in response
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_airtime_line_amount_input_greeting_mentions_network_without_full_prompt() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_line_amount_greeting",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="Hi",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_airtime"],
            fields_by_task={"t_airtime": ["recipient_phone", "amount"]},
            prompt="Sure. Which Airtel line should I buy airtime for, and how much?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"network": "Airtel"},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)
    response = updates["outbox"][0]["text"]

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].attempts == 1
    assert response == render_message(
        "orchestrator.execution.input_greeting_airtime_line_amount_network",
        "en",
        {"network": "Airtel"},
    )
    assert "Which Airtel line should I buy airtime for, and how much?" not in response
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED


@pytest.mark.asyncio
async def test_airtime_amount_reply_in_line_amount_prompt_skips_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_airtime_line_amount_amount_reply",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="4k",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_airtime"],
            fields_by_task={"t_airtime": ["recipient_phone", "amount"]},
            prompt="Sure. Which Airtel line should I buy airtime for, and how much?",
        ),
        loaded_context={"language": "en"},
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"network": "Airtel"},
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailIfRouterCalledPlanner()},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED


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
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    narration="monthly allowance",
                    reason="collective update",
                )
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_mum"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_mum"].payload["confirmation"] == {"confirmed": False}
    assert updates["tasks"]["t_mum"].payload["narration"] == "monthly allowance"
    assert updates["tasks"]["t_mum"].payload["authored_narration"] == "monthly allowance"
    assert "idempotency_key" not in updates["tasks"]["t_mum"].payload
    assert updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_gaines"].payload["confirmation"] == {"confirmed": False}
    assert updates["tasks"]["t_gaines"].payload["narration"] == "monthly allowance"
    assert updates["tasks"]["t_gaines"].payload["authored_narration"] == "monthly allowance"
    assert "idempotency_key" not in updates["tasks"]["t_gaines"].payload
    assert set(updates["last_interrupt"].task_ids) == {"t_mum", "t_gaines"}


@pytest.mark.asyncio
async def test_confirmation_continue_flow_scopes_multi_recipient_narration_updates_per_task() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_multi_confirm_note",
        phone_number="2348066666778",
        channel="whatsapp",
        last_message_text="The one for mum is allowance and tolu is transport",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_mum", "t_tolu"]),
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
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Tolu",
                    "idempotency_key": "idem-tolu",
                    "confirmation": {"summary": "Confirm Tolu", "snapshot": {"amount": 10000, "recipient_name": "Tolu"}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    updates=[
                        PendingActionTargetedUpdate(
                            target_texts=["mum"],
                            fields=PendingActionFieldUpdates(narration="allowance"),
                        ),
                        PendingActionTargetedUpdate(
                            target_texts=["tolu"],
                            fields=PendingActionFieldUpdates(narration="transport"),
                        ),
                    ],
                    reason="recipient-scoped narration update",
                )
            )
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_mum"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_mum"].payload["narration"] == "allowance"
    assert updates["tasks"]["t_mum"].payload["authored_narration"] == "allowance"
    assert updates["tasks"]["t_mum"].payload["user_note"] == "allowance"
    assert updates["tasks"]["t_mum"].payload["previous_confirmation_snapshot"] == {
        "amount": 10000,
        "recipient_name": "Mum",
    }
    assert updates["tasks"]["t_tolu"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_tolu"].payload["narration"] == "transport"
    assert updates["tasks"]["t_tolu"].payload["authored_narration"] == "transport"
    assert updates["tasks"]["t_tolu"].payload["user_note"] == "transport"
    assert updates["tasks"]["t_tolu"].payload["previous_confirmation_snapshot"] == {
        "amount": 10000,
        "recipient_name": "Tolu",
    }
    assert set(updates["last_interrupt"].task_ids) == {"t_mum", "t_tolu"}


@pytest.mark.asyncio
async def test_confirmation_continue_flow_scopes_multi_recipient_amount_and_narration_without_bleed() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_multi_confirm_note_amount",
        phone_number="2348066666779",
        channel="whatsapp",
        last_message_text="The one for mum is allowance and tolu is transport also make tolu 5k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_mum", "t_tolu"]),
        tasks={
            "t_mum": TaskSpec(
                id="t_mum",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Mum",
                    "amount": 10000,
                    "idempotency_key": "idem-mum",
                    "confirmation": {"summary": "Confirm Mum", "snapshot": {"amount": 10000, "recipient_name": "Mum"}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Tolu",
                    "amount": 10000,
                    "idempotency_key": "idem-tolu",
                    "confirmation": {"summary": "Confirm Tolu", "snapshot": {"amount": 10000, "recipient_name": "Tolu"}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    updates=[
                        PendingActionTargetedUpdate(
                            target_texts=["mum"],
                            fields=PendingActionFieldUpdates(narration="allowance"),
                        ),
                        PendingActionTargetedUpdate(
                            target_texts=["tolu"],
                            fields=PendingActionFieldUpdates(narration="transport", amount=5000),
                        ),
                    ],
                    reason="scoped multi-transfer edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_mum"].payload["narration"] == "allowance"
    assert updates["tasks"]["t_mum"].payload["amount"] == 10000
    assert updates["tasks"]["t_tolu"].payload["narration"] == "transport"
    assert updates["tasks"]["t_tolu"].payload["amount"] == 5000
    assert updates["tasks"]["t_tolu"].payload["user_note"] == "transport"
    assert updates["tasks"]["t_tolu"].payload["narration"] != "transport also make tolu 5k"


@pytest.mark.asyncio
async def test_confirmation_continue_flow_scopes_same_as_amount_to_target_recipient_only() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_multi_confirm_same_as",
        phone_number="2348066666790",
        channel="whatsapp",
        last_message_text="Change the amount for tolu to same as gaines",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "amount": 10000,
                    "idempotency_key": "idem-gaines",
                    "confirmation": {"summary": "Confirm Gaines", "snapshot": {"amount": 10000, "recipient_name": "Gaines"}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "amount": 5000,
                    "idempotency_key": "idem-tolu",
                    "confirmation": {"summary": "Confirm Tolu", "snapshot": {"amount": 5000, "recipient_name": "Tolu Adebayo"}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "idempotency_key": "idem-airtime",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    target_texts=["tolu"],
                    amount=10000,
                    reason="same-as amount resolved by semantic edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_gaines"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_gaines"].payload["amount"] == 10000
    assert updates["tasks"]["t_gaines"].payload["confirmation"]["summary"] == "Confirm Gaines"
    assert updates["tasks"]["t_airtime"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_tolu"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_tolu"].payload["amount"] == 10000
    assert updates["tasks"]["t_tolu"].payload["pending_user_message"] == "Send 10000 to Tolu Adebayo"
    assert updates["last_interrupt"].task_ids == ["t_tolu"]


@pytest.mark.asyncio
async def test_confirmation_continue_flow_rerenders_multi_transfer_summary_from_scoped_task_payloads() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_multi_confirm_rerender",
        phone_number="2348066666789",
        channel="whatsapp",
        last_message_text="The one for mum is allowance and tolu is transport also make tolu 5k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_mum", "t_tolu"]),
        tasks={
            "t_mum": TaskSpec(
                id="t_mum",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Mum",
                    "recipient_resolved_name": "Mercy Johnson",
                    "recipient_bank_name": "Opay",
                    "recipient_account": "8162511023",
                    "amount": 10000,
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm Mum", "snapshot": {"amount": 10000, "recipient_name": "Mum"}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adedayo",
                    "recipient_bank_name": "First Bank",
                    "recipient_account": "0760505261",
                    "amount": 10000,
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm Tolu", "snapshot": {"amount": 10000, "recipient_name": "Tolu"}},
                },
            ),
        },
        waves=[["t_mum", "t_tolu"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    interrupt_updates = await handle_pending_interrupt(
        state,
        {
            "configurable": {
                "task_planner": _PendingEditOnlyPlanner(
                    PendingActionEditDecision(
                        operation="update_fields",
                        confidence=0.95,
                        detected_language="English",
                        updates=[
                            PendingActionTargetedUpdate(
                                target_texts=["mum"],
                                fields=PendingActionFieldUpdates(narration="allowance"),
                            ),
                            PendingActionTargetedUpdate(
                                target_texts=["tolu"],
                                fields=PendingActionFieldUpdates(narration="transport", amount=5000),
                            ),
                        ],
                        reason="scoped multi-transfer edit",
                    )
                )
            },
            "recursion_limit": 50,
        },
    )

    rerender_state = state.model_copy(
        update={
            "tasks": interrupt_updates["tasks"],
            "pending_interrupt": interrupt_updates["pending_interrupt"],
            "last_interrupt": interrupt_updates["last_interrupt"],
        },
        deep=True,
    )
    execution_updates = await advance_wave(
        rerender_state,
        {
            "configurable": {"services": {"transfer": _TransferConfirmationRenderWorker()}},
            "recursion_limit": 50,
        },
    )

    confirmation_entry = next(entry for entry in execution_updates["outbox"] if entry["type"] == "request_confirmation")
    summary = confirmation_entry["summary"]

    assert "Confirm Transfers (2)" in summary
    assert "Total out: ₦15,000" in summary
    assert "₦10,000 → Mum (Mercy Johnson)" in summary
    assert "Narration: Allowance" in summary
    assert "₦5,000 → Tolu (Tolu Adedayo)" in summary
    assert "Narration: Transport" in summary


@pytest.mark.asyncio
async def test_confirmation_continue_flow_clarifies_ambiguous_scoped_multi_recipient_update() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_multi_confirm_ambiguous",
        phone_number="2348066666780",
        channel="whatsapp",
        last_message_text="The one is allowance and make it 5k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_mum", "t_tolu"]),
        tasks={
            "t_mum": TaskSpec(
                id="t_mum",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"recipient_name": "Mum", "confirmation": {"summary": "Confirm Mum"}},
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"recipient_name": "Tolu", "confirmation": {"summary": "Confirm Tolu"}},
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingEditOnlyPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    amount=5000,
                    narration="allowance",
                    reason="ambiguous scoped multi-transfer edit",
                )
            ),
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is not None
    assert updates["outbox"][0]["text"] == "Which recipient did you mean?"
    assert updates["tasks"]["t_mum"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_tolu"].stage == TaskStage.AWAITING_CONFIRMATION


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
async def test_confirmation_balance_query_switches_to_account_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_7c_balance",
        phone_number="2348077777780",
        channel="whatsapp",
        last_message_text="Whats my access balance",
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
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert len(updates["stashed_sessions"]) == 1
    task_ids = list(updates["tasks"].keys())
    assert len(task_ids) == 1
    assert updates["tasks"][task_ids[0]].type == "account"
    assert updates["tasks"][task_ids[0]].payload["message"] == "Whats my access balance"


@pytest.mark.asyncio
async def test_auth_balance_query_switches_to_account_without_router() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_7d_balance",
        phone_number="2348077777781",
        channel="whatsapp",
        last_message_text="What my balance in access bank",
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"confirmation": {"summary": "Authorize transfer"}},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_AUTH", interrupt_policy="BLOCK")],
        active_domain="transfer",
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert len(updates["stashed_sessions"]) == 1
    task_ids = list(updates["tasks"].keys())
    assert len(task_ids) == 1
    assert updates["tasks"][task_ids[0]].type == "account"
    assert updates["tasks"][task_ids[0]].payload["message"] == "What my balance in access bank"


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
async def test_confirmation_guarded_router_approval_advances_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_guarded_approve",
        phone_number="2348088888898",
        channel="whatsapp",
        last_message_text="make we proceed now",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
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
            decision="approve_flow",
            confidence=0.93,
            detected_language="Pidgin",
            target_intent=None,
            target_mode=None,
            reason="guarded approval",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_AUTH
    assert updates["tasks"]["t1"].payload["confirmation"]["confirmed"] is True


@pytest.mark.asyncio
async def test_confirmation_guarded_router_approval_blocks_modification_text() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_guarded_approve_block",
        phone_number="2348088888899",
        channel="whatsapp",
        last_message_text="make it 10k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
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
            decision="approve_flow",
            confidence=0.95,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="unsafe approval",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload.get("confirmation", {}).get("confirmed") is not True


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
async def test_pin_auth_approval_text_does_not_authorize_transfer() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_pin_text_guard",
        phone_number="2348099999998",
        channel="whatsapp",
        last_message_text="yes please",
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter PIN"),
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
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="text approval cannot authorize pin",
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_AUTH


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
async def test_schedule_update_confirmation_without_auth_advances_to_execution() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_schedule_no_auth",
        phone_number="2348010101013",
        channel="whatsapp",
        last_message_text="yes",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm Schedule Update"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="schedule",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "edit_scheduled_transaction",
                    "schedule_edit_requires_auth": False,
                    "confirmation": {"summary": "Confirm schedule update", "confirmed": False},
                },
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
    assert updates["tasks"]["t1"].payload["confirmation"]["confirmed"] is True


@pytest.mark.asyncio
async def test_schedule_update_confirmation_with_auth_waits_for_pin() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_schedule_auth",
        phone_number="2348010101014",
        channel="whatsapp",
        last_message_text="yes",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], prompt="Confirm Schedule Update"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="schedule",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "edit_scheduled_transaction",
                    "schedule_edit_requires_auth": True,
                    "confirmation": {"summary": "Confirm schedule update", "confirmed": False},
                },
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
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_AUTH
    assert updates["tasks"]["t1"].payload["confirmation"]["confirmed"] is True


@pytest.mark.asyncio
async def test_callback_pin_verified_auto_approves_schedule_auth_without_router_call() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_schedule_pin",
        phone_number="2348010101015",
        channel="whatsapp",
        last_message_text=None,
        last_callback={"pin_verified": True, "flow_type": "schedule"},
        pin_verified=True,
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="schedule",
                stage=TaskStage.AWAITING_AUTH,
                payload={
                    "action": "edit_scheduled_transaction",
                    "schedule_edit_requires_auth": True,
                    "confirmation": {"summary": "Confirm schedule update", "confirmed": True},
                },
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": _FailIfRouterCalledPlanner()}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXECUTING


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
async def test_input_interrupt_exhaustion_resets_after_three_failed_attempts() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_retry_budget",
        phone_number="2348010101199",
        channel="whatsapp",
        last_message_text="the blue one",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["source_account_id"]},
            prompt="Which account would you like to use?",
            attempts=2,
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 5000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner = _RouteOnlyPlanner(
        InterruptRouteDecision(
            decision="unclear",
            confidence=0.51,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="reply did not resolve the requested input",
        )
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["session_stack"] == []
    assert updates["final_response"] == render_message("orchestrator.execution.input_attempts_exhausted", "en")
    assert updates["outbox"][0]["type"] == "say"
    assert updates["outbox"][0]["text"] == render_message("orchestrator.execution.input_attempts_exhausted", "en")


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
async def test_status_query_data_recap_includes_plan_amount_network_and_line() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_data_recap",
        phone_number="2348010101191",
        channel="whatsapp",
        last_message_text="where did we stop",
        pending_interrupt=PendingInterrupt(
            kind="confirmation",
            task_ids=["data_status"],
            prompt="Review data purchase",
        ),
        tasks={
            "data_status": TaskSpec(
                id="data_status",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "plan_name": "MTN 5 GB data bundle",
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "source_bank_name": "Access Bank",
                    "idempotency_key": "idem-data-status",
                },
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
    assert updates["tasks"]["data_status"].stage == TaskStage.AWAITING_CONFIRMATION
    response = updates["outbox"][0]["text"]
    assert "data flow" in response
    assert "plan MTN 5 GB data bundle" in response
    assert "amount ₦3,500" in response
    assert "network MTN" in response
    assert "line 08162511023" in response
    assert "source Access Bank" in response
    assert "Next step: confirm to continue." in response


@pytest.mark.asyncio
async def test_status_query_airtime_recap_includes_amount_network_and_line() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_airtime_recap",
        phone_number="2348010101192",
        channel="whatsapp",
        last_message_text="where did we stop",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["airtime_status"],
            fields_by_task={"airtime_status": ["source_account_id"]},
        ),
        tasks={
            "airtime_status": TaskSpec(
                id="airtime_status",
                type="airtime",
                stage=TaskStage.RESOLVED,
                payload={
                    "amount": 2000,
                    "network": "AIRTEL",
                    "recipient_phone": "08021234567",
                    "source_bank_name": "GTBank",
                    "idempotency_key": "idem-airtime-status",
                },
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
    assert updates["tasks"]["airtime_status"].stage == TaskStage.RESOLVED
    response = updates["outbox"][0]["text"]
    assert "airtime flow" in response
    assert "amount ₦2,000" in response
    assert "network Airtel" in response
    assert "line 08021234567" in response
    assert "source GTBank" in response
    assert "Next step: provide source account selection." in response
    assert "Pick the source account by tapping it or replying with the number." in response


@pytest.mark.asyncio
async def test_status_query_data_plan_recap_includes_option_hint() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_data_choice_recap",
        phone_number="2348010101196",
        channel="whatsapp",
        last_message_text="where did we stop",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["data_choice_recap"],
            fields_by_task={"data_choice_recap": ["data_plan_id"]},
        ),
        tasks={
            "data_choice_recap": TaskSpec(
                id="data_choice_recap",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 2000,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "source_bank_name": "Access Bank",
                },
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

    response = updates["outbox"][0]["text"]
    assert updates["pending_interrupt"] is not None
    assert "data flow" in response
    assert "amount ₦2,000" in response
    assert "network MTN" in response
    assert "line 08162511023" in response
    assert "Next step: provide data plan choice." in response
    assert "Pick a data plan by replying with the option number." in response
    assert "data_plan_id" not in response


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
async def test_status_query_data_preference_requirements_uses_natural_slot_copy() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_data_preference",
        phone_number="2348010101193",
        channel="whatsapp",
        last_message_text="what do you need from me",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["data_preference"],
            fields_by_task={"data_preference": ["data_plan_preference"]},
        ),
        tasks={
            "data_preference": TaskSpec(
                id="data_preference",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"network": "MTN", "target_phone": "08162511023"},
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

    response = updates["outbox"][0]["text"]
    assert updates["pending_interrupt"] is not None
    assert "I still need: budget or data size." in response
    assert "Reply with a budget or size, like 2k or 5GB." in response
    assert "data_plan_preference" not in response


@pytest.mark.asyncio
async def test_status_query_data_plan_choice_requirements_uses_option_copy() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_data_plan_choice",
        phone_number="2348010101194",
        channel="whatsapp",
        last_message_text="what do you need from me",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["data_choice"],
            fields_by_task={"data_choice": ["data_plan_id"]},
        ),
        tasks={
            "data_choice": TaskSpec(
                id="data_choice",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"network": "MTN", "target_phone": "08162511023"},
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

    response = updates["outbox"][0]["text"]
    assert updates["pending_interrupt"] is not None
    assert "I still need: data plan choice." in response
    assert "Pick a data plan by replying with the option number." in response
    assert "data_plan_id" not in response


@pytest.mark.asyncio
async def test_status_query_airtime_line_and_network_requirements_uses_mobile_slot_copy() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_status_airtime_line_network",
        phone_number="2348010101195",
        channel="whatsapp",
        last_message_text="what do you need from me",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["airtime_line"],
            fields_by_task={"airtime_line": ["recipient_phone", "network"]},
        ),
        tasks={
            "airtime_line": TaskSpec(
                id="airtime_line",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 2000},
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

    response = updates["outbox"][0]["text"]
    assert updates["pending_interrupt"] is not None
    assert "I still need: phone line, mobile network." in response
    assert "Reply with the phone number, or say my line if it is for you." in response
    assert "Reply with the network, like MTN, Airtel, Glo, or 9mobile." in response
    assert "recipient_phone" not in response


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
    assert updates["outbox"][0]["text"] == "Please share the account number and bank for Tolu (TOLU ADEDAYO)."
    assert "I found" not in updates["outbox"][0]["text"]
