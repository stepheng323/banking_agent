"""Latency guard tests for interrupt routing context prompt growth."""

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
    assert "required_fields=" in context
