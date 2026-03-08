"""Latency guard tests for interrupt routing context prompt growth."""

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.interrupt import (
    INTERRUPT_CONTEXT_MAX_CHARS,
    _build_interrupt_context,
)


def test_interrupt_context_is_bounded_and_keeps_prefix_fields() -> None:
    huge_prompt = "please continue " + ("now " * 1200)
    large_fields = {
        "t1": [f"field_{idx}_{'x' * 80}" for idx in range(30)],
        "t2": [f"another_{idx}_{'y' * 80}" for idx in range(30)],
    }
    state = OrchestratorState(
        user_id="u_interrupt_cap_1",
        phone_number="2348099999991",
        channel="whatsapp",
        loaded_context={
            "accounts": [
                {
                    "bank_name": f"Bank {idx}",
                    "account_number": f"000000000{idx}",
                    "mandate_status": "ready",
                }
                for idx in range(1, 25)
            ],
            "history": [{"role": "user", "content": "z" * 5000} for _ in range(10)],
        },
    )

    context = _build_interrupt_context(
        state=state,
        kind="input",
        task_ids=["t1", "t2"],
        current_task_types={"transfer"},
        fields_by_task=large_fields,
        prompt=huge_prompt,
    )

    assert len(context) <= INTERRUPT_CONTEXT_MAX_CHARS
    assert context.startswith("Active Flow:")
    assert "active_task_state=" in context
    assert "required_fields=" in context
    assert "prompt=" in context
    assert "...[truncated]" in context


def test_interrupt_context_prefix_survives_tail_truncation() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_cap_2",
        phone_number="2348099999992",
        channel="whatsapp",
        loaded_context={
            "accounts": [
                {
                    "bank_name": f"Bank {idx}",
                    "account_number": f"000000000{idx}",
                    "mandate_status": "pending",
                    "extra_data": {
                        "transfer_destinations": [
                            {"bank_name": "Dest", "account_number": "1234567890"} for _ in range(8)
                        ]
                    },
                }
                for idx in range(1, 40)
            ]
        },
    )

    context = _build_interrupt_context(
        state=state,
        kind="confirmation",
        task_ids=["t3"],
        current_task_types={"airtime"},
        fields_by_task={"t3": ["amount", "phone"]},
        prompt="Confirm this purchase",
    )

    assert len(context) <= INTERRUPT_CONTEXT_MAX_CHARS
    assert "Active Flow: confirmation required for tasks ['t3']" in context
    assert "active_task_state=" in context
    assert "required_fields=" in context


def test_interrupt_context_includes_compact_active_task_state() -> None:
    state = OrchestratorState(
        user_id="u_interrupt_cap_3",
        phone_number="2348099999993",
        channel="whatsapp",
        tasks={
            "t9": TaskSpec(
                id="t9",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 16000,
                    "recipient_name": "Mum",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                    "confirmation": {
                        "summary": "Confirm transfer",
                        "snapshot": {"amount": 16000, "recipient_name": "Mum"},
                    },
                },
            ),
        },
    )

    context = _build_interrupt_context(
        state=state,
        kind="confirmation",
        task_ids=["t9"],
        current_task_types={"transfer"},
        fields_by_task={"t9": []},
        prompt="Confirm transfer",
    )

    assert "active_task_state=" in context
    assert "\"t9\"" in context
    assert "\"recipient_name\": \"Mum\"" in context
    assert "\"has_recipient_account\": true" in context
