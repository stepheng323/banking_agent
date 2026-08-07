from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts import (
    _build_missing_field_interrupt_updates,
)
from banking.presentation.formatters.transaction_intent_lines import format_intent_line


def _state(tasks: dict[str, TaskSpec]) -> OrchestratorState:
    return OrchestratorState(
        user_id="u_mixed_input",
        phone_number="+2348000000000",
        tasks=tasks,
        waves=[["transfer_1", "airtime_1"]],
    )


def test_mixed_missing_input_keeps_complete_contract_and_guides_selection() -> None:
    tasks = {
        "transfer_1": TaskSpec(
            id="transfer_1",
            type="transfer",
            stage=TaskStage.EXTRACTED,
            payload={"action": "send_money", "amount": 2_000, "recipient_name": "Tolu"},
        ),
        "airtime_1": TaskSpec(
            id="airtime_1",
            type="airtime",
            stage=TaskStage.EXTRACTED,
            payload={"action": "buy_airtime", "amount": None, "recipient_phone": "08162511023", "is_self": True},
        ),
    }
    state = _state(tasks)
    accumulator = ExecutionAccumulator(tasks)
    accumulator.add_missing_fields("transfer_1", ["beneficiary_id"])
    accumulator.add_prompt("Choose the Tolu recipient.", "transfer_1")
    accumulator.add_details(
        "transfer_1",
        {
            "candidates": [{"beneficiary_id": "bene-1", "label": "Tolu Access · Access Bank · ···0001"}],
            "options": [{"id": "bene:bene-1", "title": "1. Tolu Access"}],
        },
    )
    accumulator.add_missing_fields("airtime_1", ["amount"])
    accumulator.add_prompt("How much airtime should I buy for your line?", "airtime_1")

    updates = _build_missing_field_interrupt_updates(
        state=state,
        current_wave=["transfer_1", "airtime_1"],
        agg=accumulator,
        locale="en",
    )

    interrupt = updates["pending_interrupt"]
    assert interrupt.task_ids == ["transfer_1", "airtime_1"]
    assert interrupt.fields_by_task == {"transfer_1": ["beneficiary_id"], "airtime_1": ["amount"]}
    assert interrupt.batch_input is not None
    assert interrupt.batch_input.focused_slot.task_id == "transfer_1"
    assert interrupt.batch_input.focused_slot.field == "beneficiary_id"
    outbox = updates["outbox"]
    assert outbox[0]["type"] == "show_options"
    assert "Choose the recipient for ₦2,000 to “Tolu”." in outbox[0]["title"]
    assert "Still needed: the airtime amount for your line." in outbox[0]["title"]
    assert "₦0" not in outbox[0]["title"]
    assert "₦0" not in outbox[0]["title"]


def test_absent_transaction_amount_is_never_rendered_as_zero_value_intent() -> None:
    assert format_intent_line("airtime", {"recipient_phone": "08162511023", "amount": None}) == ""
    assert format_intent_line("transfer", {"recipient_name": "Tolu", "amount": 0}) == ""
