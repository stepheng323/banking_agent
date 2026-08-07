from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_summary import (
    _build_confirmation_gate_body_blocks,
    _build_confirmation_gate_summary,
)
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import (
    _force_recipient_selection_before_confirmation,
    _maybe_mark_recipient_review_required,
)
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import (
    maybe_request_recipient_review,
    recipient_review_signature,
)
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import _handle_transaction_outcome
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_finalize import (
    _remove_suppressed_actionable_entries,
)
from banking.runtime.results import TransactionOutcome, TransactionResult


def test_confirmation_is_blocked_when_candidates_survive_with_review_flag_false() -> None:
    """A stale review flag must not authorize an unresolved beneficiary."""
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "recipient_name": "Tolu Adebayo",
            "recipient_review_required": False,
            "beneficiary_candidates": [
                {"beneficiary_id": "bene-gtb", "recipient_name": "Tolu GTB"},
                {"beneficiary_id": "bene-access", "recipient_name": "Tolu Access"},
            ],
        },
    )
    result = _force_recipient_selection_before_confirmation(
        task=task,
        result=TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm transfer",
            confirmation_snapshot={"amount": 2000},
        ),
        locale="en",
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["beneficiary_id"]
    assert result.confirmation_snapshot is None


def test_option_surface_never_ships_a_confirmation_card_in_the_same_outbox() -> None:
    entries = _remove_suppressed_actionable_entries(
        [
            {"type": "show_options", "options": [{"id": "bene-1"}]},
            {"type": "request_confirmation", "task_ids": ["t1"]},
            {"type": "say", "text": "Choose one."},
        ]
    )

    assert [entry["type"] for entry in entries] == ["show_options", "say"]


def _state_with_tasks(tasks: dict[str, TaskSpec]) -> OrchestratorState:
    return OrchestratorState(
        user_id="user-1",
        phone_number="+2348000000000",
        tasks=tasks,
        waves=[list(tasks)],
    )


def test_processing_transfer_receipt_sets_final_status_processing() -> None:
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXECUTING,
        payload={"amount": 30000, "recipient_name": "Mom"},
    )
    agg = ExecutionAccumulator({"t1": task})

    _handle_transaction_outcome(
        task,
        "t1",
        TransactionResult(outcome=TransactionOutcome.OK, receipt={"status": "processing"}),
        agg,
        confirmation_gate="snapshot",
        default_error=None,
    )

    assert task.stage == TaskStage.COMPLETED
    assert task.payload["final_status"] == "processing"


def test_recipient_review_blocks_before_confirmation_or_funding() -> None:
    tasks = {
        "t1": TaskSpec(
            id="t1",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "recipient_name": "mom",
                "recipient_resolved_name": "Fatima Zahra Musa",
                "recipient_bank_name": "Wema Bank",
                "recipient_account": "0123452221",
                "recipient_review_required": True,
            },
        ),
        "t2": TaskSpec(
            id="t2",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "recipient_name": "ay",
                "recipient_resolved_name": "Yusuf Ibrahim",
                "recipient_bank_name": "OPay",
                "recipient_account": "9876544362",
                "recipient_review_required": True,
            },
        ),
    }
    state = _state_with_tasks(tasks)
    agg = ExecutionAccumulator(tasks)

    updates = maybe_request_recipient_review(state=state, current_wave=["t1", "t2"], agg=agg)

    assert updates is not None
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert interrupt.fields_by_task == {
        "t1": ["recipient_review_confirmed"],
        "t2": ["recipient_review_confirmed"],
    }
    prompt = updates["outbox"][0]["text"]
    assert "Recipient review" in prompt
    assert "mom → Fatima Zahra Musa" in prompt
    assert "Wema Bank • ****2221" in prompt
    assert "ay → Yusuf Ibrahim" in prompt
    assert "OPay • ****4362" in prompt
    assert "Reply yes to continue" in prompt


def test_recipient_review_keeps_ready_self_transfer_visible_in_batch() -> None:
    tasks = {
        "external": TaskSpec(
            id="external",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "amount": 2000,
                "recipient_name": "Tolu",
                "recipient_resolved_name": "Tolu Adebayo",
                "recipient_bank_name": "Access Bank",
                "recipient_account": "2010000001",
                "recipient_review_required": True,
            },
        ),
        "self": TaskSpec(
            id="self",
            type="transfer",
            stage=TaskStage.EXTRACTED,
            payload={
                "amount": 5000,
                "is_self": True,
                "recipient_bank_name": "Access Bank",
                "recipient_resolved_name": "My Access Bank Account",
                "recipient_account": "2010000001",
            },
        ),
    }
    state = _state_with_tasks(tasks)
    agg = ExecutionAccumulator(tasks)

    updates = maybe_request_recipient_review(state=state, current_wave=["external", "self"], agg=agg)

    assert updates is not None
    prompt = updates["outbox"][0]["text"]
    assert "₦2,000" not in prompt  # the recipient-review list remains recipient-focused
    assert "₦5,000" in prompt
    assert "My Access Bank" in prompt
    assert "Access Bank • ****0001" in prompt
    assert "₦₦" not in prompt
    assert updates["pending_interrupt"].task_ids == ["external"]


def test_final_batch_confirmation_includes_external_and_self_transfer() -> None:
    tasks = {
        "external": TaskSpec(
            id="external",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "amount": 2_000,
                "recipient_name": "Tolu",
                "recipient_resolved_name": "Tolu Adebayo",
                "recipient_bank_name": "GTBank",
                "recipient_account": "2010000002",
                "confirmation": {
                    "snapshot": {
                        "amount": 2_000,
                        "recipient_name": "Tolu Adebayo",
                        "recipient_bank": "GTBank",
                        "recipient_account": "2010000002",
                        "sourceBank": "First Bank",
                        "sourceAccount": "2010000001",
                    }
                },
            },
        ),
        "self": TaskSpec(
            id="self",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "amount": 5_000,
                "is_self": True,
                "recipient_bank_name": "Access Bank",
                "recipient_resolved_name": "My Access Bank Account",
                "recipient_account": "2010000001",
                "confirmation": {
                    "snapshot": {
                        "amount": 5_000,
                        "is_self": True,
                        "recipient_name": "My Access Bank Account",
                        "recipient_bank": "Access Bank",
                        "recipient_account": "2010000001",
                        "sourceBank": "GTBank",
                        "sourceAccount": "0000000002",
                    }
                },
            },
        ),
    }
    state = _state_with_tasks(tasks)

    summary = _build_confirmation_gate_summary(
        state=state,
        task_ids=["external", "self"],
        locale="en",
        accounts=[],
    )

    assert "₦2,000" in summary
    assert "Tolu Adebayo" in summary
    assert "₦5,000" in summary
    assert "My Access Bank" in summary
    assert "₦7,000" in summary


def test_single_and_batch_transfer_reviews_use_the_same_structured_rows() -> None:
    task = TaskSpec(
        id="single",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 2_000,
            "recipient_name": "Tolu",
            "recipient_resolved_name": "Tolu Adebayo",
            "recipient_bank_name": "GTBank",
            "recipient_account": "2010000002",
            "source_bank_name": "GTBank",
            "source_account_number": "6000000002",
            "confirmation": {
                "snapshot": {
                    "amount": 2_000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_bank": "GTBank",
                    "recipient_account": "2010000002",
                    "sourceBank": "GTBank",
                    "sourceAccount": "6000000002",
                }
            },
        },
    )
    blocks = _build_confirmation_gate_body_blocks(
        state=_state_with_tasks({"single": task}),
        task_ids=["single"],
        locale="en",
        accounts=[],
    )

    assert blocks is not None
    assert blocks[0] == {"type": "key_value", "label": "Total", "value": "₦2,000"}
    assert blocks[1]["title"] == "₦2,000 → Tolu (Tolu Adebayo)"
    assert "details" not in blocks[1]
    assert blocks[2] == {"type": "text", "text": "From: GTBank (···0002)"}


def test_recipient_review_waits_for_unresolved_batch_sibling() -> None:
    tasks = {
        "t_mom": TaskSpec(
            id="t_mom",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "recipient_name": "mom",
                "recipient_resolved_name": "Fatima Zahra Musa",
                "recipient_bank_name": "Wema Bank",
                "recipient_account": "0123452221",
                "recipient_review_required": True,
            },
        ),
        "t_ay": TaskSpec(
            id="t_ay",
            type="transfer",
            stage=TaskStage.EXTRACTED,
            payload={
                "recipient_name": "ay",
                "recipient_bank_name": "OPay",
                "recipient_account": "9876544362",
            },
        ),
    }
    state = _state_with_tasks(tasks)
    agg = ExecutionAccumulator(tasks)

    updates = maybe_request_recipient_review(state=state, current_wave=["t_mom", "t_ay"], agg=agg)

    assert updates is None


def test_recipient_review_does_not_replace_unresolved_beneficiary_selection() -> None:
    tasks = {
        "t_ready": TaskSpec(
            id="t_ready",
            type="transfer",
            stage=TaskStage.AWAITING_CONFIRMATION,
            payload={
                "recipient_name": "ay",
                "recipient_resolved_name": "Yusuf Ibrahim",
                "recipient_bank_name": "OPay",
                "recipient_account": "9876544362",
                "recipient_review_required": True,
            },
        ),
        "t_choice": TaskSpec(
            id="t_choice",
            type="transfer",
            stage=TaskStage.EXTRACTED,
            payload={"recipient_name": "Tolu Adebayo", "amount": 2_000},
        ),
    }
    state = _state_with_tasks(tasks)
    agg = ExecutionAccumulator(tasks)
    agg.add_missing_fields("t_choice", ["beneficiary_id"])

    updates = maybe_request_recipient_review(state=state, current_wave=["t_ready", "t_choice"], agg=agg)

    assert updates is None


def test_recipient_review_skips_when_current_signature_already_confirmed() -> None:
    payload = {
        "recipient_name": "mom",
        "recipient_resolved_name": "Fatima Zahra Musa",
        "recipient_bank_name": "Wema Bank",
        "recipient_account": "0123452221",
        "recipient_resolution_provider": "mono",
        "recipient_resolution_mode": "single_source",
        "recipient_review_confirmed": True,
        "recipient_review_required": True,
    }
    payload["recipient_review_signature"] = recipient_review_signature(payload)
    tasks = {"t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.AWAITING_CONFIRMATION, payload=payload)}
    state = _state_with_tasks(tasks)
    agg = ExecutionAccumulator(tasks)

    updates = maybe_request_recipient_review(state=state, current_wave=["t1"], agg=agg)

    assert updates is None


def test_recipient_review_carries_forward_when_resolution_mode_changes_but_identity_matches() -> None:
    previous_payload = {
        "recipient_name": "mom",
        "recipient_resolved_name": "Fatima Zahra Musa",
        "recipient_bank_name": "Wema Bank",
        "recipient_account": "0123452221",
        "recipient_resolution_provider": "mono",
        "recipient_resolution_mode": "single_source",
        "recipient_review_confirmed": True,
    }
    previous_payload["recipient_review_signature"] = recipient_review_signature(previous_payload)
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            **previous_payload,
            "recipient_bank_code": "000014",
            "recipient_bank_code_provider": "flutterwave",
            "recipient_resolution_mode": "pooled",
        },
    )

    review_required = _maybe_mark_recipient_review_required(
        task=task,
        required_fields=[],
        result_patch={
            "recipient_bank_code": "000014",
            "recipient_bank_code_provider": "flutterwave",
            "recipient_resolution_mode": "pooled",
            "recipient_resolved_name": "Fatima Zahra Musa",
        },
        previous_payload=previous_payload,
        previous_signature=previous_payload["recipient_review_signature"],
    )

    assert review_required is False
    assert task.payload["recipient_review_confirmed"] is True
    assert task.payload["recipient_review_required"] is False
    assert task.payload["recipient_review_signature"] == recipient_review_signature(task.payload)


def test_recipient_review_required_when_resolution_mode_change_returns_different_identity() -> None:
    previous_payload = {
        "recipient_name": "mom",
        "recipient_resolved_name": "Fatima Zahra Musa",
        "recipient_bank_name": "Wema Bank",
        "recipient_account": "0123452221",
        "recipient_resolution_provider": "mono",
        "recipient_resolution_mode": "single_source",
        "recipient_review_confirmed": True,
    }
    previous_payload["recipient_review_signature"] = recipient_review_signature(previous_payload)
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            **previous_payload,
            "recipient_bank_code": "000014",
            "recipient_bank_code_provider": "flutterwave",
            "recipient_resolution_provider": "flutterwave",
            "recipient_resolution_mode": "pooled",
            "recipient_resolved_name": "Pastor Bright",
        },
    )

    review_required = _maybe_mark_recipient_review_required(
        task=task,
        required_fields=[],
        result_patch={
            "recipient_bank_code": "000014",
            "recipient_bank_code_provider": "flutterwave",
            "recipient_resolution_provider": "flutterwave",
            "recipient_resolution_mode": "pooled",
            "recipient_resolved_name": "Pastor Bright",
        },
        previous_payload=previous_payload,
        previous_signature=previous_payload["recipient_review_signature"],
    )

    assert review_required is True
    assert task.payload["recipient_review_confirmed"] is False
    assert task.payload["recipient_review_required"] is True
    assert "recipient_review_signature" not in task.payload


def test_self_transfer_resolution_does_not_create_recipient_review() -> None:
    task = TaskSpec(
        id="self",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "is_self": True,
            "amount": 5_000,
            "recipient_bank_name": "Access Bank",
            "recipient_account": "2010000001",
        },
    )

    review_required = _maybe_mark_recipient_review_required(
        task=task,
        required_fields=["recipient_bank_name"],
        result_patch={
            "recipient_bank_name": "Access Bank",
            "recipient_account": "2010000001",
            "recipient_resolved_name": "My Access Bank Account",
        },
        previous_payload=dict(task.payload),
        previous_signature=None,
    )

    assert review_required is False
    assert task.payload.get("recipient_review_required") is None
