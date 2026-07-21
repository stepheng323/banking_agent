from __future__ import annotations

import time

from apps.chat.src.agent.orchestrator.context.models import (
    ContextEntity,
    ContextFrame,
    ContextFrameType,
    EntityType,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_set_mutations import (
    build_set_mutation_result_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    context_frame_state_view,
)
from shared.types.conversation_sets import (
    ConversationSetState,
    EntitySelectionRef,
    SetAmountAllocation,
    SetScopeDelta,
)
from shared.types.planner import ContextFrameFollowupDecision


def _frame(domain: str, count: int = 3, *, include_set_state: bool = True) -> ContextFrame:
    frame_id = f"{domain}-frame"
    entity_type = {
        "beneficiary": EntityType.BENEFICIARY,
        "schedule": EntityType.SCHEDULE,
        "linked_account": EntityType.ACCOUNT,
        "support": EntityType.SUPPORT_TICKET,
    }[domain]
    frame_type = {
        "beneficiary": ContextFrameType.BENEFICIARY_LIST,
        "schedule": ContextFrameType.SCHEDULE_LIST,
        "linked_account": ContextFrameType.ACCOUNT_LIST,
        "support": ContextFrameType.SUPPORT_TICKET_LIST,
    }[domain]
    refs = [
        EntitySelectionRef(
            entity_type="support_ticket" if domain == "support" else domain,
            entity_id=f"{domain}-{index}",
            frame_id=frame_id,
            display_label=f"Item {index} · ···{index:04d}",
            version_token=f"version-{index}",
        )
        for index in range(1, count + 1)
    ]
    metadata = {}
    if include_set_state:
        metadata["conversation_set_state"] = ConversationSetState(
            domain=domain,
            mentioned_refs=refs,
            last_result_refs=refs,
        ).model_dump(mode="json")
    return ContextFrame(
        frame_id=frame_id,
        frame_type=frame_type,
        items=[
            ContextEntity(
                entity_type=entity_type,
                entity_id=ref.entity_id,
                label=ref.display_label,
                data={"version_token": ref.version_token},
            )
            for ref in refs
        ],
        created_at_ts=int(time.time()),
        metadata=metadata,
    )


def test_beneficiary_delete_materializes_id_backed_review_request() -> None:
    frame = _frame("beneficiary")
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])
    decision = ContextFrameFollowupDecision(
        decision="delete_beneficiary",
        confidence=0.95,
        set_scope_delta=SetScopeDelta(operation="replace", selection_indices=[1, 3]),
    )

    result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        decision,
        "remove the first and third",
        locale="en",
    )

    assert result is not None and result.tasks is not None
    task = next(iter(result.tasks.values()))
    assert task.type == "beneficiary"
    assert task.payload["selected_entity_ids"] == ["beneficiary-1", "beneficiary-3"]
    assert [target["entity_id"] for target in task.payload["bulk_mutation"]["targets"]] == [
        "beneficiary-1",
        "beneficiary-3",
    ]


def test_frame_without_set_state_cannot_authorize_mutations() -> None:
    frame = _frame("schedule", include_set_state=False)
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])
    decision = ContextFrameFollowupDecision(
        decision="cancel_schedule",
        confidence=0.95,
        selection_index=1,
    )

    result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        decision,
        "cancel the first one",
        locale="en",
    )

    assert result is not None
    assert result.tasks is None
    assert "refresh" in (result.response or "").lower()


def test_set_default_requires_exactly_one_selected_account() -> None:
    frame = _frame("linked_account")
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])
    decision = ContextFrameFollowupDecision(
        decision="set_default_account",
        confidence=0.95,
        set_scope_delta=SetScopeDelta(operation="replace", selection_indices=[1, 2]),
    )

    result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        decision,
        "make those default",
        locale="en",
    )

    assert result is not None
    assert result.tasks is None
    assert "exactly one" in (result.response or "").lower()


def test_multi_beneficiary_transfer_requires_explicit_allocation_for_each_target() -> None:
    frame = _frame("beneficiary")
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])
    incomplete = ContextFrameFollowupDecision(
        decision="transfer_beneficiaries",
        confidence=0.95,
        set_scope_delta=SetScopeDelta(operation="replace", selection_indices=[1, 2]),
        set_amount_allocations=[SetAmountAllocation(selection_index=1, amount=1000)],
    )

    clarification = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        incomplete,
        "send 1k to the first and 2k to the second",
        locale="en",
    )
    assert clarification is not None and clarification.tasks is None
    assert "each selected beneficiary" in (clarification.response or "")

    complete = incomplete.model_copy(
        update={
            "set_amount_allocations": [
                SetAmountAllocation(selection_index=1, amount=1000),
                SetAmountAllocation(selection_index=2, amount=2000),
            ]
        }
    )
    result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        complete,
        "send 1k to the first and 2k to the second",
        locale="en",
    )

    assert result is not None and result.tasks is not None
    assert [task.payload["amount"] for task in result.tasks.values()] == [1000, 2000]
    assert [task.payload["beneficiary_id"] for task in result.tasks.values()] == [
        "beneficiary-1",
        "beneficiary-2",
    ]


def test_schedule_pause_and_resume_materialize_reviewed_bulk_requests() -> None:
    frame = _frame("schedule")
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])

    for decision_name, expected_action, expected_bulk_action in (
        ("pause_schedule", "pause_scheduled_transaction", "pause"),
        ("resume_schedule", "resume_scheduled_transaction", "resume"),
    ):
        decision = ContextFrameFollowupDecision(
            decision=decision_name,
            confidence=0.95,
            set_scope_delta=SetScopeDelta(operation="replace", selection_indices=[1, 2]),
        )
        result = build_set_mutation_result_for_view(
            context_frame_state_view(state),
            frame,
            decision,
            f"{expected_bulk_action} the first two",
            locale="en",
        )

        assert result is not None and result.tasks is not None
        task = next(iter(result.tasks.values()))
        assert task.payload["action"] == expected_action
        assert task.payload["bulk_mutation"]["action"] == expected_bulk_action
        assert len(task.payload["bulk_mutation"]["targets"]) == 2


def test_beneficiary_rename_requires_one_stable_reference_and_new_alias() -> None:
    frame = _frame("beneficiary")
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])
    decision = ContextFrameFollowupDecision(
        decision="rename_beneficiary",
        confidence=0.95,
        selection_index=2,
        new_alias="Tolu Work",
    )

    result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        decision,
        "rename the second one Tolu Work",
        locale="en",
    )

    assert result is not None and result.tasks is not None
    task = next(iter(result.tasks.values()))
    assert task.payload["action"] == "rename_beneficiary"
    assert task.payload["new_alias"] == "Tolu Work"
    assert task.payload["beneficiary_selection_ref"]["entity_id"] == "beneficiary-2"


def test_ticket_note_and_close_use_owner_scoped_stable_ticket_reference() -> None:
    frame = _frame("support", count=1, include_set_state=False)
    frame.items[0].data["ticket_code"] = "SUP-123"
    state = OrchestratorState(user_id="user-1", phone_number="2348000000000", context_frames=[frame])

    note_result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        ContextFrameFollowupDecision(
            decision="append_ticket_note",
            confidence=0.95,
            selection_index=1,
            ticket_note="The transfer is still missing.",
        ),
        "add that the transfer is still missing",
        locale="en",
    )
    assert note_result is not None and note_result.tasks is not None
    note_task = next(iter(note_result.tasks.values()))
    assert note_task.payload["action"] == "append_support_ticket_note"
    assert note_task.payload["ticket_id"] == "support-1"
    assert note_task.payload["ticket_note"] == "The transfer is still missing."

    close_result = build_set_mutation_result_for_view(
        context_frame_state_view(state),
        frame,
        ContextFrameFollowupDecision(decision="close_ticket", confidence=0.95, selection_index=1),
        "close it",
        locale="en",
    )
    assert close_result is not None and close_result.tasks is not None
    close_task = next(iter(close_result.tasks.values()))
    assert close_task.payload["action"] == "close_support_ticket"
    assert close_task.payload["ticket_code"] == "SUP-123"
