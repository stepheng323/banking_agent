from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import (
    _maybe_mark_recipient_review_required,
)
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import (
    maybe_request_recipient_review,
    recipient_review_signature,
)
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import _handle_transaction_outcome
from banking.runtime.results import TransactionOutcome, TransactionResult


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
