"""Typed targeting for self-transfer confirmation edits."""

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.batch_slot_fill import _looks_like_batch_slot_text
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_target_resolution import (
    _target_task_ids_from_decision,
)
from shared.types.planner import PendingActionEditDecision


def _state(*, removed: bool = False) -> OrchestratorState:
    self_task = TaskSpec(
        id="self_transfer",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 5000,
            "is_self": True,
            "recipient_bank_name": "Access Bank",
            "recipient_account": "6000000003",
        },
    )
    external_task = TaskSpec(
        id="external_transfer",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 2000,
            "is_self": False,
            "recipient_name": "Tolu Adebayo",
            "recipient_bank_name": "Access Bank",
            "recipient_account": "2010000001",
        },
    )
    if removed:
        return OrchestratorState(
            user_id="u_self_target",
            phone_number="2348000000000",
            waves=[["external_transfer"]],
            current_wave_index=0,
            tasks={"external_transfer": external_task},
            removed_confirmation_tasks={
                "self_transfer": {
                    "task": self_task,
                    "wave_index": 0,
                    "position": 0,
                }
            },
        )
    return OrchestratorState(
        user_id="u_self_target",
        phone_number="2348000000000",
        waves=[["self_transfer", "external_transfer"]],
        current_wave_index=0,
        tasks={"self_transfer": self_task, "external_transfer": external_task},
    )


def test_self_transfer_reference_targets_only_typed_self_task() -> None:
    decision = PendingActionEditDecision(
        operation="remove_tasks",
        confidence=0.95,
        target_texts=["self transaction"],
    )

    assert _target_task_ids_from_decision(
        decision=decision,
        task_ids=["self_transfer", "external_transfer"],
        state=_state(),
    ) == ["self_transfer"]


def test_self_reference_overrides_broad_transfer_type_hint() -> None:
    decision = PendingActionEditDecision(
        operation="remove_tasks",
        confidence=0.95,
        target_types=["transfer"],
        target_texts=["self transaction"],
    )

    assert _target_task_ids_from_decision(
        decision=decision,
        task_ids=["self_transfer", "external_transfer"],
        state=_state(),
    ) == ["self_transfer"]


def test_self_transfer_reference_can_restore_removed_task() -> None:
    decision = PendingActionEditDecision(
        operation="restore_tasks",
        confidence=0.95,
        target_texts=["my Access account"],
    )

    assert _target_task_ids_from_decision(
        decision=decision,
        task_ids=["self_transfer"],
        state=_state(removed=True),
        removed=True,
    ) == ["self_transfer"]


def test_self_transfer_reference_uses_confirmation_snapshot_when_payload_is_legacy() -> None:
    state = _state()
    task = state.tasks["self_transfer"]
    task.payload.pop("is_self")
    task.payload["confirmation"] = {"snapshot": {"is_self": True}}
    decision = PendingActionEditDecision(
        operation="remove_tasks",
        confidence=0.95,
        target_texts=["self transfer"],
    )

    assert _target_task_ids_from_decision(
        decision=decision,
        task_ids=["self_transfer", "external_transfer"],
        state=state,
    ) == ["self_transfer"]


def test_confirmation_edit_is_not_misclassified_as_batch_account_input() -> None:
    assert not _looks_like_batch_slot_text("Wait, remove the self transaction")
    assert _looks_like_batch_slot_text("8067892221 Wema for mum")
