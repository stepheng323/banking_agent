"""ID-backed reviewed mutations originating from conversational set frames."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE,
    canonical_decision,
    decision_target_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
)
from banking.presentation.i18n.renderer import render_message
from shared.types.conversation_sets import (
    BulkMutationRequest,
    ConversationSetState,
    MutationSetDomain,
    SetScopeDelta,
    apply_set_scope,
    resolve_delta_references,
)
from shared.types.planner import ContextFrameFollowupDecision


@dataclass(frozen=True, slots=True)
class SetMutationResult:
    recent_domain_focus: str
    response: str | None = None
    tasks: dict[str, TaskSpec] | None = None
    waves: list[list[str]] | None = None


def _selection_delta(decision: ContextFrameFollowupDecision) -> SetScopeDelta:
    if decision.set_scope_delta is not None:
        return decision.set_scope_delta
    indices = [decision.selection_index] if decision.selection_index is not None else []
    target = decision_target_text(decision)
    return SetScopeDelta(
        operation="replace" if indices or target else "preserve",
        selection_indices=indices,
        target_labels=[target] if target else [],
    )


def _next_task_id(state_view: ContextFrameStateView, domain: str) -> str:
    index = 1
    task_id = f"context_{domain}_set_mutation_{index}"
    while task_id in state_view.task_ids:
        index += 1
        task_id = f"context_{domain}_set_mutation_{index}"
    return task_id


def build_set_mutation_result_for_view(
    state_view: ContextFrameStateView,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    text: str,
    *,
    locale: str,
) -> SetMutationResult | None:
    """Materialize a mutation only from current stable frame references."""

    semantic_decision = canonical_decision(decision.decision)
    if semantic_decision in {"append_ticket_note", "close_ticket"}:
        if (
            frame.frame_type != ContextFrameType.SUPPORT_TICKET_LIST
            or decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE
        ):
            return None
        selected_entity = None
        if decision.selection_index is not None and 1 <= decision.selection_index <= len(frame.items):
            selected_entity = frame.items[decision.selection_index - 1]
        else:
            target = decision_target_text(decision).casefold()
            matches = [item for item in frame.items if target and target in item.label.casefold()]
            if len(matches) == 1:
                selected_entity = matches[0]
            elif not target and len(frame.items) == 1:
                selected_entity = frame.items[0]
        if selected_entity is None or not selected_entity.entity_id:
            return SetMutationResult(
                recent_domain_focus="support",
                response=render_message("support.ticket.select", locale),
            )
        ticket_payload: dict[str, object] = {
            "action": (
                "append_support_ticket_note" if semantic_decision == "append_ticket_note" else "close_support_ticket"
            ),
            "message": text,
            "instruction": text,
            "ticket_id": selected_entity.entity_id,
            "ticket_code": selected_entity.data.get("ticket_code"),
        }
        if semantic_decision == "append_ticket_note":
            if not decision.ticket_note:
                return SetMutationResult(
                    recent_domain_focus="support",
                    response=render_message("support.ticket.note_prompt", locale),
                )
            ticket_payload["ticket_note"] = decision.ticket_note
        task_id = _next_task_id(state_view, "support")
        task = TaskSpec(id=task_id, type="support", stage=TaskStage.DRAFT, payload=ticket_payload)
        return SetMutationResult(
            recent_domain_focus="support",
            tasks={task_id: task},
            waves=[[task_id]],
        )

    mapping: dict[
        str,
        tuple[
            ContextFrameType,
            MutationSetDomain,
            Literal["delete", "cancel", "edit", "pause", "resume", "unlink"] | None,
            str,
        ],
    ] = {
        "delete_beneficiary": (ContextFrameType.BENEFICIARY_LIST, "beneficiary", "delete", "delete_beneficiary"),
        "rename_beneficiary": (ContextFrameType.BENEFICIARY_LIST, "beneficiary", None, "rename_beneficiary"),
        "unlink_account": (ContextFrameType.ACCOUNT_LIST, "linked_account", "unlink", "unlink"),
        "set_default_account": (ContextFrameType.ACCOUNT_LIST, "linked_account", None, "set_default"),
        "relink_account": (ContextFrameType.ACCOUNT_LIST, "linked_account", None, "reinitiate_mandate"),
        "transfer_beneficiaries": (ContextFrameType.BENEFICIARY_LIST, "beneficiary", None, "send_money"),
        "cancel_schedule": (ContextFrameType.SCHEDULE_LIST, "schedule", "cancel", "cancel_scheduled_transaction"),
        "edit_schedule": (ContextFrameType.SCHEDULE_LIST, "schedule", "edit", "edit_scheduled_transaction"),
        "pause_schedule": (ContextFrameType.SCHEDULE_LIST, "schedule", "pause", "pause_scheduled_transaction"),
        "resume_schedule": (ContextFrameType.SCHEDULE_LIST, "schedule", "resume", "resume_scheduled_transaction"),
    }
    selected_mapping = mapping.get(semantic_decision)
    if selected_mapping is None or decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        return None
    expected_frame, domain, bulk_action, task_action = selected_mapping
    if frame.frame_type != expected_frame:
        return None

    raw_state = frame.metadata.get("conversation_set_state")
    if not isinstance(raw_state, dict):
        return SetMutationResult(
            recent_domain_focus=domain,
            response=render_message("conversation_set.stale_selection", locale),
        )
    try:
        set_state = ConversationSetState.model_validate(raw_state)
    except ValueError:
        return SetMutationResult(
            recent_domain_focus=domain,
            response=render_message("conversation_set.stale_selection", locale),
        )

    delta = _selection_delta(decision)
    visible_refs = list(set_state.last_result_refs)
    resolved, unresolved = resolve_delta_references(delta, visible_refs=visible_refs)
    selected = apply_set_scope(
        set_state,
        delta,
        resolved_refs=resolved,
        all_refs=visible_refs,
    )
    if unresolved or not selected:
        return SetMutationResult(
            recent_domain_focus=domain,
            response=render_message("conversation_set.selection_required", locale),
        )
    if len(selected) > 5:
        return SetMutationResult(
            recent_domain_focus=domain,
            response=render_message("conversation_set.mutation_limit", locale, {"count": 5}),
        )
    if semantic_decision in {"set_default_account", "relink_account", "rename_beneficiary"} and len(selected) != 1:
        return SetMutationResult(
            recent_domain_focus=domain,
            response=render_message("conversation_set.single_selection_required", locale),
        )

    if semantic_decision == "transfer_beneficiaries":
        allocations: dict[str, object] = {}
        for allocation in decision.set_amount_allocations:
            allocation_delta = SetScopeDelta(
                operation="replace",
                selection_indices=([allocation.selection_index] if allocation.selection_index is not None else []),
                target_labels=[allocation.target_label] if allocation.target_label else [],
            )
            allocation_refs, allocation_unresolved = resolve_delta_references(
                allocation_delta,
                visible_refs=visible_refs,
            )
            if allocation_unresolved or len(allocation_refs) != 1:
                return SetMutationResult(
                    recent_domain_focus="transfer",
                    response=render_message("conversation_set.allocation_required", locale),
                )
            allocations[allocation_refs[0].entity_id] = allocation.amount
        selected_ids = {ref.entity_id for ref in selected}
        if set(allocations) != selected_ids:
            return SetMutationResult(
                recent_domain_focus="transfer",
                response=render_message("conversation_set.allocation_required", locale),
            )
        tasks: dict[str, TaskSpec] = {}
        wave: list[str] = []
        for index, ref in enumerate(selected, start=1):
            task_id = _next_task_id(state_view, f"transfer_{index}")
            task = TaskSpec(
                id=task_id,
                type="transfer",
                stage=TaskStage.DRAFT,
                payload={
                    "action": "send_money",
                    "message": text,
                    "instruction": text,
                    "beneficiary_id": ref.entity_id,
                    "beneficiary_selection_ref": ref.model_dump(mode="json"),
                    "amount": allocations[ref.entity_id],
                    "confirmation": {"confirmed": False},
                },
            )
            tasks[task_id] = task
            wave.append(task_id)
        return SetMutationResult(
            recent_domain_focus="transfer",
            tasks=tasks,
            waves=[wave],
        )

    task_domain: Literal["account", "beneficiary", "schedule"]
    if domain == "linked_account":
        task_domain = "account"
    elif domain == "beneficiary":
        task_domain = "beneficiary"
    else:
        task_domain = "schedule"
    task_id = _next_task_id(state_view, task_domain)
    payload: dict[str, object] = {
        "action": task_action,
        "message": text,
        "instruction": text,
        "selected_entity_ids": [ref.entity_id for ref in selected],
        "conversation_set_state": set_state.model_copy(
            update={"focused_ref": selected[0] if len(selected) == 1 else None, "last_result_refs": selected}
        ).model_dump(mode="json", exclude_none=True),
    }
    if semantic_decision == "rename_beneficiary":
        if not decision.new_alias:
            return SetMutationResult(
                recent_domain_focus="beneficiary",
                response=render_message("beneficiary.rename.ask_alias", locale),
            )
        payload["beneficiary_selection_ref"] = selected[0].model_dump(mode="json")
        payload["new_alias"] = decision.new_alias
    if bulk_action is not None:
        signature = ":".join([frame.frame_id, bulk_action, *(ref.entity_id for ref in selected)])
        request = BulkMutationRequest(
            domain=domain,
            action=bulk_action,
            targets=selected,
            idempotency_key=f"set:{sha256(signature.encode()).hexdigest()[:24]}",
            patch=(
                decision.schedule_edit_delta.model_dump(mode="json", exclude_none=True)
                if bulk_action == "edit" and decision.schedule_edit_delta is not None
                else {}
            ),
        )
        payload["bulk_mutation"] = request.model_dump(mode="json", exclude_none=True)
    else:
        payload["identifier"] = selected[0].entity_id

    task = TaskSpec(id=task_id, type=task_domain, stage=TaskStage.DRAFT, payload=payload)
    return SetMutationResult(
        recent_domain_focus=task_domain,
        tasks={task_id: task},
        waves=[[task_id]],
    )


__all__ = ["SetMutationResult", "build_set_mutation_result_for_view"]
