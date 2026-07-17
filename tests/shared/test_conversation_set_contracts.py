from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.types.conversation_sets import (
    BulkMutationRequest,
    ConversationSetState,
    EntitySelectionRef,
    SetScopeDelta,
    apply_set_scope,
    resolve_delta_references,
)


def _ref(index: int, *, entity_type: str = "beneficiary") -> EntitySelectionRef:
    return EntitySelectionRef(
        entity_type=entity_type,
        entity_id=f"id-{index}",
        frame_id="frame-1",
        display_label=f"Tolu {index} · Bank · ···{index:04d}",
        version_token=f"v-{index}",
    )


def test_set_resolution_supports_ordinals_and_unique_labels() -> None:
    refs = [_ref(index) for index in range(1, 6)]

    resolved, unresolved = resolve_delta_references(
        SetScopeDelta(operation="replace", selection_indices=[2], target_labels=["Tolu 4"]),
        visible_refs=refs,
    )

    assert [ref.entity_id for ref in resolved] == ["id-2", "id-4"]
    assert unresolved == []


def test_set_resolution_does_not_choose_duplicate_labels() -> None:
    refs = [_ref(1), _ref(2)]
    refs[1] = refs[1].model_copy(update={"display_label": refs[0].display_label})

    resolved, unresolved = resolve_delta_references(
        SetScopeDelta(operation="replace", target_labels=["Tolu 1"]),
        visible_refs=refs,
    )

    assert resolved == []
    assert unresolved == ["Tolu 1"]


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("preserve", ["id-3"]),
        ("recent_two", ["id-2", "id-3"]),
        ("mentioned", ["id-1", "id-2", "id-3"]),
        ("last_result", ["id-2", "id-3"]),
        ("all", ["id-1", "id-2", "id-3", "id-4"]),
    ],
)
def test_named_set_scopes_are_deterministic(operation: str, expected: list[str]) -> None:
    refs = [_ref(index) for index in range(1, 5)]
    state = ConversationSetState(
        domain="beneficiary",
        focused_ref=refs[2],
        mentioned_refs=refs[:3],
        last_result_refs=refs[1:3],
    )

    selected = apply_set_scope(
        state,
        SetScopeDelta(operation=operation),  # type: ignore[arg-type]
        all_refs=refs,
    )

    assert [ref.entity_id for ref in selected] == expected


def test_add_remove_and_replace_are_bounded_and_deduplicated() -> None:
    refs = [_ref(index) for index in range(1, 21)]
    state = ConversationSetState(
        domain="beneficiary",
        mentioned_refs=refs,
        last_result_refs=refs[:5],
    )

    added = apply_set_scope(state, SetScopeDelta(operation="add"), resolved_refs=[refs[4], refs[5]])
    removed = apply_set_scope(state, SetScopeDelta(operation="remove"), resolved_refs=[refs[1], refs[3]])
    replaced = apply_set_scope(state, SetScopeDelta(operation="replace"), resolved_refs=refs)

    assert [ref.entity_id for ref in added] == ["id-1", "id-2", "id-3", "id-4", "id-5", "id-6"]
    assert [ref.entity_id for ref in removed] == ["id-1", "id-3", "id-5"]
    assert len(replaced) == 20


def test_reviewed_mutations_are_capped_at_five_and_domain_bound() -> None:
    refs = [_ref(index) for index in range(1, 7)]

    request = BulkMutationRequest(
        domain="beneficiary",
        action="delete",
        targets=refs[:5],
        idempotency_key="delete-1",
    )
    assert len(request.targets) == 5

    with pytest.raises(ValidationError):
        BulkMutationRequest(
            domain="beneficiary",
            action="delete",
            targets=refs,
            idempotency_key="delete-2",
        )

    with pytest.raises(ValidationError):
        BulkMutationRequest(
            domain="schedule",
            action="cancel",
            targets=refs[:1],
            idempotency_key="cancel-1",
        )


def test_unversioned_frames_cannot_construct_mutation_reference() -> None:
    with pytest.raises(ValidationError):
        EntitySelectionRef.model_validate(
            {
                "entity_type": "beneficiary",
                "entity_id": "id-1",
                "frame_id": "old-frame",
                "display_label": "Tolu · Bank · ···0001",
            }
        )
