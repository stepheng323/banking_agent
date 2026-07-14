"""Active-flow question handling during pending interrupts."""

from typing import Any, Literal

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import (
    ActiveSession,
    AuthorizationContext,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from shared.types.planner import ActiveFlowQuestionType, InterruptRouteDecision, PendingActionEditDecision


class _RoutePlanner:
    def __init__(self, route: InterruptRouteDecision) -> None:
        self.route = route
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
        return self.route

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


def _route(
    question_type: ActiveFlowQuestionType,
    *,
    target_field: str | None = None,
    unsafe_reason: str | None = None,
) -> InterruptRouteDecision:
    return InterruptRouteDecision(
        decision="active_flow_question",
        confidence=0.9,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        question_type=question_type,
        target_field=target_field,
        unsafe_reason=unsafe_reason,
        reason="test active-flow question",
    )


def _state(
    *,
    task_type: Literal[
        "transfer",
        "airtime",
        "data",
        "query",
        "support",
        "account",
        "beneficiary",
        "schedule",
        "faq",
    ],
    kind: Literal["input", "confirmation", "auth"],
    text: str,
    required_fields: list[str],
    payload: dict[str, Any],
    pin_verified: bool = False,
) -> OrchestratorState:
    task_id = f"{task_type}_1"
    stage = TaskStage.AWAITING_CONFIRMATION
    if kind == "auth":
        stage = TaskStage.AWAITING_AUTH
    elif kind == "input":
        stage = TaskStage.DRAFT
    session_state: Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH"] = (
        "WAITING_FOR_AUTH" if kind == "auth" else "WAITING_FOR_INPUT"
    )
    return OrchestratorState(
        user_id=f"u_{task_type}_{kind}",
        phone_number="+2348000000000",
        last_message_text=text,
        tasks={
            task_id: TaskSpec(
                id=task_id,
                type=task_type,
                stage=stage,
                payload=payload,
            )
        },
        waves=[[task_id]],
        pending_interrupt=PendingInterrupt(
            kind=kind,
            task_ids=[task_id],
            fields_by_task={task_id: required_fields},
            auth_method="pin" if kind == "auth" else None,
        ),
        session_stack=[
            ActiveSession(domain=task_type, state=session_state, interrupt_policy="BLOCK"),
        ],
        active_domain=task_type,
        pin_verified=pin_verified,
    )


def _config(planner: object | None = None) -> RunnableConfig:
    configurable: dict[str, object] = {}
    if planner is not None:
        configurable["task_planner"] = planner
    return {"configurable": configurable, "recursion_limit": 50}


def _say_text(updates: dict[str, object]) -> str:
    outbox = updates["outbox"]
    assert isinstance(outbox, list)
    first = outbox[0]
    assert isinstance(first, dict)
    return str(first["text"])


@pytest.mark.asyncio
async def test_active_flow_question_explains_transfer_bank_requirement() -> None:
    state = _state(
        task_type="transfer",
        kind="input",
        text="Why do you need the bank?",
        required_fields=["recipient_account", "recipient_bank_name"],
        payload={"amount": 20_000, "recipient_name": "Mum"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="recipient_bank_name"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert planner.route_calls == 1
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["tasks"] == state.tasks
    assert "verify the account number" in _say_text(updates)
    assert "Send the recipient bank name to continue." in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_blocks_future_reversal_claims() -> None:
    state = _state(
        task_type="transfer",
        kind="confirmation",
        text="Can I get it back later?",
        required_fields=[],
        payload={
            "amount": 20_000,
            "recipient_name": "Mum",
            "recipient_resolved_name": "FATIMA ZAHRA MUSA",
            "recipient_bank_name": "Opay",
            "recipient_account": "8067892221",
        },
    )
    planner = _RoutePlanner(
        _route("unsupported_or_unsafe", unsafe_reason="future_reversal"),
    )

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates).lower()
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "cancel now" in response
    assert "cannot be reversed or refunded" in response
    assert "reply yes" not in response


@pytest.mark.asyncio
async def test_active_flow_question_explains_pin_authorization_in_auth_interrupt() -> None:
    state = _state(
        task_type="transfer",
        kind="auth",
        text="Why PIN?",
        required_fields=[],
        payload={"amount": 20_000, "recipient_name": "Mum"},
    )
    planner = _RoutePlanner(_route("auth_pin_reason", target_field="pin"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "PIN authorizes this transaction" in response
    assert "Complete PIN authorization to continue, or say cancel." in response
    assert "Reply yes" not in response


@pytest.mark.asyncio
async def test_active_flow_question_explains_data_network_requirement() -> None:
    state = _state(
        task_type="data",
        kind="input",
        text="Why network?",
        required_fields=["network"],
        payload={"amount": 2_000, "target_phone": "08030000000"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="network"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "right biller" in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_explains_airtime_phone_requirement() -> None:
    state = _state(
        task_type="airtime",
        kind="input",
        text="Why phone?",
        required_fields=["recipient_phone"],
        payload={"amount": 1_000, "network": "MTN"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="recipient_phone"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "line to recharge" in _say_text(updates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_field", "expected"),
    [
        ("amount", "The amount is"),
        ("network", "The network is MTN"),
        ("phone", "The phone line is 08030000000"),
        ("source_account_id", "The source account is Access Bank (...0003)"),
    ],
)
async def test_active_flow_question_answers_airtime_current_values(target_field: str, expected: str) -> None:
    state = _state(
        task_type="airtime",
        kind="confirmation",
        text="Which detail is this?",
        required_fields=[],
        payload={
            "amount": 1_000,
            "network": "MTN",
            "recipient_phone": "08030000000",
            "source_bank_name": "Access Bank",
            "source_account_number": "0000000003",
        },
    )
    planner = _RoutePlanner(_route("current_value", target_field=target_field))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert expected in _say_text(updates)
    assert "Reply yes to continue to authorization, or say cancel." in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_verified_confirmation_tail_skips_authorization() -> None:
    state = _state(
        task_type="transfer",
        kind="confirmation",
        text="How much?",
        required_fields=[],
        payload={
            "amount": 20_000,
            "recipient_name": "Mum",
            "recipient_resolved_name": "FATIMA ZAHRA MUSA",
            "recipient_bank_name": "Opay",
            "recipient_account": "8067892221",
        },
        pin_verified=True,
    )
    state = state.model_copy(
        update={
            "authorization_context": AuthorizationContext(
                idempotency_key="idem-transfer",
                flow_type="transfer",
                authorized_task_idempotency_keys=["idem-transfer"],
            ),
            "pending_interrupt": state.pending_interrupt.model_copy(
                update={"authorization_idempotency_key": "idem-transfer"}
            )
            if state.pending_interrupt is not None
            else None,
        }
    )
    planner = _RoutePlanner(_route("current_value", target_field="amount"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "The amount is" in response
    assert "Reply yes to continue, or say cancel." in response
    assert "continue to authorization" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("confirmation", "Reply yes to continue to authorization, or say cancel."),
        ("auth", "Complete PIN authorization to continue, or say cancel."),
    ],
)
async def test_status_query_return_to_flow_tail_is_pin_aware(
    kind: Literal["confirmation", "auth"],
    expected: str,
) -> None:
    state = _state(
        task_type="transfer",
        kind=kind,
        text="where are we",
        required_fields=[],
        payload={
            "amount": 20_000,
            "recipient_name": "Mum",
            "recipient_resolved_name": "FATIMA ZAHRA MUSA",
            "recipient_bank_name": "Opay",
            "recipient_account": "8067892221",
        },
    )

    updates = await handle_pending_interrupt(state, _config())

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert expected in response


@pytest.mark.asyncio
async def test_active_flow_question_explains_query_date_requirement() -> None:
    state = _state(
        task_type="query",
        kind="input",
        text="Why date range?",
        required_fields=["date_range"],
        payload={"query": "transactions"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="date_range"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "search the right transactions" in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_explains_support_reference_requirement() -> None:
    state = _state(
        task_type="support",
        kind="input",
        text="Why transaction?",
        required_fields=["transaction_reference"],
        payload={"issue_type": "failed_transfer"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="transaction_reference"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "support can check the right payment" in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_explains_account_selection_requirement() -> None:
    state = _state(
        task_type="account",
        kind="input",
        text="Why account?",
        required_fields=["account_id"],
        payload={"action": "set_default"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="account_id"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "which linked account" in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_account_requirements_use_natural_copy() -> None:
    state = _state(
        task_type="account",
        kind="input",
        text="What do you need?",
        required_fields=["account_id"],
        payload={"action": "set_default"},
    )
    planner = _RoutePlanner(_route("requirements"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "account selection" in response
    assert "Pick the account" in response


@pytest.mark.asyncio
async def test_active_flow_question_beneficiary_ambiguity_lists_options() -> None:
    state = _state(
        task_type="beneficiary",
        kind="input",
        text="Which one?",
        required_fields=["beneficiary_id"],
        payload={
            "matches": [
                {"alias": "Mum", "bank_name": "Opay", "account_number": "8067892221"},
                {"alias": "Mummy", "bank_name": "Access Bank", "account_number": "0000000003"},
            ]
        },
    )
    planner = _RoutePlanner(_route("current_value", target_field="beneficiary_id"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "1. Mum Opay ...2221" in response
    assert "2. Mummy Access Bank ...0003" in response


@pytest.mark.asyncio
async def test_active_flow_question_explains_schedule_selection_requirement() -> None:
    state = _state(
        task_type="schedule",
        kind="input",
        text="Why schedule?",
        required_fields=["schedule_id"],
        payload={"action": "cancel_scheduled_transfer"},
    )
    planner = _RoutePlanner(_route("why_required", target_field="schedule_id"))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "which schedule" in _say_text(updates)


@pytest.mark.asyncio
async def test_active_flow_question_schedule_timing_uses_payload() -> None:
    state = _state(
        task_type="schedule",
        kind="confirmation",
        text="What time?",
        required_fields=[],
        payload={
            "recipient_name": "Mum",
            "schedule_start_date": "2026-06-10",
            "schedule_time_local": "09:00",
            "recurrence_type": "monthly",
            "status": "active",
        },
    )
    planner = _RoutePlanner(_route("timing_or_status", target_field="schedule_time"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "active" in response
    assert "2026-06-10 09:00 monthly" in response


@pytest.mark.asyncio
async def test_active_flow_question_faq_unknown_preserves_interrupt() -> None:
    state = _state(
        task_type="faq",
        kind="input",
        text="Is this magic?",
        required_fields=["clarification"],
        payload={"question": "card limits"},
    )
    planner = _RoutePlanner(_route("unknown"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "cannot answer that safely" in response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_field", "expected"),
    [
        ("recipient", "This is going to FATIMA ZAHRA MUSA."),
        ("amount", "The amount is"),
        ("source_account_id", "The source account is Access Bank (...0003)."),
        ("recipient_bank_name", "The recipient bank is Opay."),
    ],
)
async def test_active_flow_question_answers_transfer_confirmation_current_values(
    target_field: str,
    expected: str,
) -> None:
    state = _state(
        task_type="transfer",
        kind="confirmation",
        text="What is this?",
        required_fields=[],
        payload={
            "amount": 20_000,
            "recipient_name": "Mum",
            "recipient_resolved_name": "FATIMA ZAHRA MUSA",
            "recipient_bank_name": "Opay",
            "recipient_account": "8067892221",
            "source_bank_name": "Access Bank",
            "source_account_number": "0000000003",
        },
    )
    planner = _RoutePlanner(_route("current_value", target_field=target_field))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert expected in _say_text(updates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_field", "expected"),
    [
        ("phone", "The phone line is 08030000000."),
        ("network", "The network is Airtel."),
        ("data_plan_id", "The selected plan is 5GB Monthly for"),
    ],
)
async def test_active_flow_question_answers_data_confirmation_current_values(
    target_field: str,
    expected: str,
) -> None:
    state = _state(
        task_type="data",
        kind="confirmation",
        text="What is this?",
        required_fields=[],
        payload={
            "amount": 2_500,
            "network": "AIRTEL",
            "target_phone": "08030000000",
            "plan_name": "5GB Monthly",
        },
    )
    planner = _RoutePlanner(_route("current_value", target_field=target_field))

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert expected in _say_text(updates)


@pytest.mark.asyncio
async def test_cancel_question_does_not_cancel_pending_interrupt() -> None:
    state = _state(
        task_type="transfer",
        kind="confirmation",
        text="Can I cancel?",
        required_fields=[],
        payload={"amount": 20_000, "recipient_name": "Mum"},
    )
    planner = _RoutePlanner(
        InterruptRouteDecision(
            decision="active_flow_question",
            confidence=0.94,
            detected_language="English",
            question_type="cancellation_effect",
            target_intent=None,
            target_mode=None,
            reason="user asking about cancellation",
        )
    )

    updates = await handle_pending_interrupt(state, _config(planner))

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["tasks"] == state.tasks
    assert "No money will be sent" in _say_text(updates)


@pytest.mark.asyncio
async def test_unknown_active_flow_question_preserves_pending_interrupt() -> None:
    state = _state(
        task_type="beneficiary",
        kind="input",
        text="Is the moon involved?",
        required_fields=["beneficiary_id"],
        payload={"matches": ["Mum", "Mummy"]},
    )
    planner = _RoutePlanner(_route("unknown"))

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "cannot answer that safely" in response
    assert "beneficiary" in response


@pytest.mark.asyncio
async def test_active_flow_question_unsupported_capability_refusal() -> None:
    state = _state(
        task_type="transfer",
        kind="input",
        text="I need money abeg",
        required_fields=["amount"],
        payload={"recipient_name": "Mum"},
    )
    planner = _RoutePlanner(
        InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.9,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            reason="router misclassified need money as continue_flow",
        )
    )

    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "help with loans or lending" in response
    assert "Reply with the amount" in response

    state = _state(
        task_type="transfer",
        kind="input",
        text="Buy bitcoin for me",
        required_fields=["amount"],
        payload={"recipient_name": "Mum"},
    )
    updates = await handle_pending_interrupt(state, _config(planner))

    response = _say_text(updates)
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "help with investments or crypto" in response
    assert "Reply with the amount" in response
