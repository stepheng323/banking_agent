"""Interrupt routing call-budget tests."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from apps.chat.src.agent.workers.transfer.models.entities import TransferEntities
from apps.chat.src.agent.workers.transfer.models.extraction import TransferExtractionResult
from banking.presentation.i18n.bridge import render_cancelled_prompt
from shared.types.planner import (
    InterruptRouteDecision,
    PendingActionEditDecision,
    PlannedTask,
    PlannerOutput,
    RecipientAllocation,
    TaskParameters,
)


class _CountingPlanner:
    def __init__(self, route: InterruptRouteDecision, output: PlannerOutput) -> None:
        self._route = route
        self._output = output
        self.route_calls = 0
        self.plan_calls = 0
        self.last_context: str | None = None

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        del phone_number, text, path_label, prompt_mode
        self.route_calls += 1
        self.last_context = context
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

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text, context
        self.plan_calls += 1
        return self._output


class _TransferExtractorStub:
    def __init__(self, result: TransferExtractionResult) -> None:
        self._result = result
        self.calls = 0

    async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
        del text, smart_context
        self.calls += 1
        return self._result


class _TransferWorkerStub:
    def __init__(self, extractor: _TransferExtractorStub) -> None:
        self.extractor = extractor


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
async def test_transaction_switch_target_skips_planner_call() -> None:
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
    assert planner.plan_calls == 0
    task_ids = list(updates["tasks"].keys())
    assert len(task_ids) == 1
    assert updates["tasks"][task_ids[0]].type == "transfer"


@pytest.mark.asyncio
async def test_amount_interrupt_send_amount_reply_continues_same_transfer_without_router() -> None:
    state = OrchestratorState(
        user_id="u_budget_amount_command",
        phone_number="2348100000013",
        channel="whatsapp",
        last_message_text="Send 12k",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["amount"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "recipient_account": "0760505261",
                    "recipient_bank_name": "Opay",
                    "recipient_resolved_name": "TOLU ADEDAYO",
                    "suggested_amount": 10000,
                },
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
            reason="would be wrong for amount-only continuation",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload["recipient_account"] == "0760505261"
    assert updates["tasks"]["t1"].payload["suggested_amount"] == 10000


@pytest.mark.asyncio
async def test_transaction_switch_transfer_multi_recipient_fanout_preserved_without_planner() -> None:
    state = OrchestratorState(
        user_id="u_budget_2_multi",
        phone_number="2348100000012",
        channel="whatsapp",
        last_message_text="send 5k to mum and gains",
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
            confidence=0.95,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="fresh transfer batch request",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert len(updates["tasks"]) == 2
    assert len(updates["waves"][0]) == 2
    assert all(task.type == "transfer" for task in updates["tasks"].values())


@pytest.mark.asyncio
async def test_transaction_switch_split_retry_uses_recipient_allocations_without_planner() -> None:
    state = OrchestratorState(
        user_id="u_budget_2_split_retry",
        phone_number="2348100000014",
        channel="whatsapp",
        last_message_text="split 20k 70/30 btw mum and gaines",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["recipient_account"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Gaines", "amount": 6000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    extractor = _TransferExtractorStub(
        TransferExtractionResult(
            entities=TransferEntities(
                amount=20000,
                recipient_allocations=[
                    RecipientAllocation(recipient_name="Mum", amount=14000.0),
                    RecipientAllocation(recipient_name="Gaines", amount=6000.0),
                ],
            ),
        )
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.94,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="fresh transfer split retry",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {"transfer": _TransferWorkerStub(extractor)},
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert extractor.calls == 1
    assert "final_response" not in updates
    assert updates["pending_interrupt"] is None
    assert set(updates["tasks"].keys()) == {"interrupt_transfer_1", "interrupt_transfer_1_r2"}
    assert updates["tasks"]["interrupt_transfer_1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["interrupt_transfer_1"].payload.get("amount") == 14000.0
    assert updates["tasks"]["interrupt_transfer_1"].payload.get("recipient_allocations") is None
    assert updates["tasks"]["interrupt_transfer_1"].payload.get("explicit_split") is None
    assert updates["tasks"]["interrupt_transfer_1_r2"].payload.get("recipient_name") == "Gaines"
    assert updates["tasks"]["interrupt_transfer_1_r2"].payload.get("amount") == 6000.0
    assert updates["tasks"]["interrupt_transfer_1_r2"].payload.get("recipient_allocations") is None
    assert updates["tasks"]["interrupt_transfer_1_r2"].payload.get("explicit_split") is None


@pytest.mark.asyncio
async def test_status_query_requirements_skips_planner_and_uses_interrupt_router_only() -> None:
    state = OrchestratorState(
        user_id="u_budget_status_1",
        phone_number="2348100000091",
        channel="whatsapp",
        last_message_text="what do you need from me",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["beneficiary_id"]},
        ),
        loaded_context={
            "accounts": [{"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "8162511023"}],
        },
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
            decision="status_query",
            confidence=0.95,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type="requirements",
            reason="requirements status query",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert planner.last_context is None
    assert updates["pending_interrupt"] is not None
    assert updates["outbox"][0]["type"] == "say"
    assert "I still need: beneficiary selection." in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_status_query_recap_skips_planner_and_uses_interrupt_router_only() -> None:
    state = OrchestratorState(
        user_id="u_budget_status_2",
        phone_number="2348100000092",
        channel="whatsapp",
        last_message_text="where did we stop",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["beneficiary_id"]},
        ),
        loaded_context={
            "accounts": [{"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"}],
            "history": [{"role": "assistant", "content": "We are preparing your transfer."}],
        },
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.RESOLVED,
                payload={"recipient_name": "Tolu", "amount": 5000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="status_query",
            confidence=0.95,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type="recap",
            reason="recap status query",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert planner.last_context is None
    assert updates["pending_interrupt"] is not None
    assert updates["outbox"][0]["type"] == "say"
    assert "transfer flow" in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_ambiguous_status_wording_still_uses_interrupt_router() -> None:
    state = OrchestratorState(
        user_id="u_budget_status_3",
        phone_number="2348100000093",
        channel="whatsapp",
        last_message_text="can you remind me again please",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["beneficiary_id"]},
        ),
        loaded_context={
            "accounts": [{"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"}],
        },
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
            decision="status_query",
            confidence=0.84,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type="recap",
            reason="ambiguous recap via router",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert planner.last_context is not None
    assert "ACCOUNTS:" not in planner.last_context
    assert "BENEFICIARIES:" not in planner.last_context
    assert "Interrupt Kind: input" in planner.last_context
    assert updates["pending_interrupt"] is not None
    assert updates["outbox"][0]["type"] == "say"
    assert "transfer flow" in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_transaction_switch_transfer_schedule_action_preserved_without_planner() -> None:
    state = OrchestratorState(
        user_id="u_budget_2_sched",
        phone_number="2348100000013",
        channel="whatsapp",
        last_message_text="send 5k to mum tomorrow",
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
            confidence=0.95,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="scheduled transfer request",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    task_id = list(updates["tasks"].keys())[0]
    assert updates["tasks"][task_id].type == "transfer"
    assert updates["tasks"][task_id].payload.get("action") == "schedule_transfer"


@pytest.mark.asyncio
async def test_transaction_switch_transfer_cancel_schedule_action_preserved_without_planner() -> None:
    state = OrchestratorState(
        user_id="u_budget_2_cancel_sched",
        phone_number="2348100000014",
        channel="whatsapp",
        last_message_text="cancel my scheduled transfer 2",
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
            confidence=0.95,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="cancel schedule request",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    task_id = list(updates["tasks"].keys())[0]
    assert updates["tasks"][task_id].payload.get("action") == "cancel_scheduled_transfer"
    assert updates["tasks"][task_id].payload.get("schedule_selector") == "2"


@pytest.mark.asyncio
async def test_transaction_switch_uses_interrupt_extractor_seed() -> None:
    state = OrchestratorState(
        user_id="u_budget_2b",
        phone_number="2348100000020",
        channel="whatsapp",
        last_message_text="send 5k to mum",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter your PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={
                    "recipient_name": "Mercy",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                    "amount": 5000,
                },
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
                    instruction="Send 5000 to Mum",
                    parameters=TaskParameters(
                        amount=5000,
                        recipient="Mum",
                        recipient_account="8162511023",
                        bank_name="Zenith Bank",
                    ),
                    risk="MONEY_MOVE",
                )
            ],
        ),
    )
    extractor = _TransferExtractorStub(
        TransferExtractionResult(
            entities=TransferEntities(recipient_name="Mum", recipient_account="8162511023", bank_name="Zenith Bank"),
        )
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {"transfer": _TransferWorkerStub(extractor)},
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert extractor.calls == 1
    task_id = list(updates["tasks"].keys())[0]
    payload = updates["tasks"][task_id].payload
    assert payload.get("recipient_name") == "Mum"
    assert payload.get("recipient_account") == "8162511023"
    assert payload.get("recipient_bank_name") == "Zenith Bank"
    assert payload.get("skip_extraction") is True


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
async def test_confirmation_non_explicit_text_never_auto_approves_even_if_router_says_approve() -> None:
    state = OrchestratorState(
        user_id="u_budget_3b",
        phone_number="2348100000031",
        channel="whatsapp",
        last_message_text="Add it for feeding",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 5000,
                    "recipient_name": "Mum",
                    "confirmation": {"summary": "Confirm transfer"},
                    "idempotency_key": "idem-old",
                },
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="approve_flow",
            confidence=0.93,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="misclassified_update_as_approval",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t1"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t1"].payload.get("confirmation") == {}
    assert "idempotency_key" not in updates["tasks"]["t1"].payload


@pytest.mark.asyncio
async def test_confirmation_explicit_approval_still_advances_on_llm_route_path() -> None:
    state = OrchestratorState(
        user_id="u_budget_3c",
        phone_number="2348100000032",
        channel="whatsapp",
        last_message_text="yes",
        loaded_context={"language": "fr"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"amount": 5000, "recipient_name": "Mum"},
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="approve_flow",
            confidence=0.95,
            detected_language="French",
            target_intent=None,
            target_mode=None,
            reason="explicit_confirmation_approval",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
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


@pytest.mark.asyncio
async def test_auth_reprompt_for_mixed_task_types_uses_default_header() -> None:
    state = OrchestratorState(
        user_id="u_budget_6",
        phone_number="2348100000006",
        channel="whatsapp",
        last_message_text="hmm",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(
            kind="auth",
            task_ids=["t1", "t2"],
            auth_method="pin",
            prompt="Enter your PIN",
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_name": "Mum", "amount": 10000},
            ),
            "t2": TaskSpec(
                id="t2",
                type="airtime",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_phone": "08162511023", "amount": 5000},
            ),
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": None}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)
    outbox = updates["outbox"][0]
    assert outbox["type"] == "auth_request"
    assert set(outbox["task_ids"]) == {"t1", "t2"}
    assert outbox["header"] == "Authorize Transaction"


@pytest.mark.asyncio
async def test_auth_obvious_cancel_shortcut_resets_flow() -> None:
    state = OrchestratorState(
        user_id="u_budget_7",
        phone_number="2348100000007",
        channel="whatsapp",
        last_message_text="abort this",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter your PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_name": "Mum", "amount": 10000},
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="cancel",
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="unused cancel route",
        ),
        output=PlannerOutput(primary_intent="cancel"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["final_response"] == render_cancelled_prompt("en")


@pytest.mark.asyncio
async def test_auth_switch_intent_routes_once_and_stashes_transaction_flow() -> None:
    state = OrchestratorState(
        user_id="u_budget_8",
        phone_number="2348100000008",
        channel="whatsapp",
        last_message_text="check my balance",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter your PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_name": "Mum", "amount": 10000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.95,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="fresh account request",
        ),
        output=PlannerOutput(primary_intent="account"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    task_ids = list(updates["tasks"].keys())
    assert len(task_ids) == 1
    assert updates["tasks"][task_ids[0]].type == "account"
    assert updates["pending_interrupt"] is None
    assert len(updates.get("stashed_sessions", [])) == 1


@pytest.mark.asyncio
async def test_auth_typed_pin_text_never_authorizes_without_callback() -> None:
    state = OrchestratorState(
        user_id="u_budget_9",
        phone_number="2348100000009",
        channel="whatsapp",
        last_message_text="1234",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="auth", task_ids=["t1"], auth_method="pin", prompt="Enter your PIN"),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_AUTH,
                payload={"recipient_name": "Mum", "amount": 10000},
            )
        },
    )
    planner = _CountingPlanner(
        route=InterruptRouteDecision(
            decision="approve_flow",
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="typed_pin_text",
        ),
        output=PlannerOutput(primary_intent="transfer"),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["pending_interrupt"] is not None
    assert updates["pending_interrupt"].kind == "auth"
    assert updates["tasks"]["t1"].stage == TaskStage.AWAITING_AUTH
