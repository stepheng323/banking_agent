"""Semantic pending-confirmation edit helpers.

The LLM-facing layer classifies a user turn into a typed operation. This module
keeps the context and normalization shared, while runner-owned deterministic
code resolves task ids and applies mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.types.planner import PendingActionEditDecision

PENDING_ACTION_EDIT_MIN_CONFIDENCE = 0.55
_EDIT_TASK_TYPES = {"transfer", "airtime", "data"}


@dataclass(frozen=True, slots=True)
class PendingActionEditResolution:
    decision: PendingActionEditDecision

    @property
    def operation(self) -> str:
        return self.decision.operation


def _task_amount(payload: dict[str, Any]) -> Any:
    return payload.get("amount") or payload.get("suggested_amount")


def _task_label(task: TaskSpec) -> str:
    payload = task.payload if isinstance(task.payload, dict) else {}
    if task.type == "transfer":
        recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name") or payload.get("recipient")
        bank = payload.get("recipient_bank_name") or payload.get("bank_name")
        account = payload.get("recipient_account") or payload.get("recipient_account_number")
        return " ".join(str(part) for part in (recipient, bank, account) if part)
    if task.type in {"airtime", "data"}:
        phone = payload.get("phone") or payload.get("recipient_phone") or payload.get("target_phone")
        network = payload.get("network")
        plan = payload.get("plan")
        return " ".join(str(part) for part in (task.type, phone, network, plan) if part)
    return task.type


def _task_context_entry(task_id: str, task: TaskSpec) -> dict[str, Any]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    entry = {
        "task_id": task_id,
        "type": task.type,
        "stage": task.stage.value if hasattr(task.stage, "value") else str(task.stage),
        "label": _task_label(task),
        "amount": _task_amount(payload),
        "recipient_name": payload.get("recipient_name"),
        "recipient_resolved_name": payload.get("recipient_resolved_name"),
        "recipient_account": payload.get("recipient_account") or payload.get("recipient_account_number"),
        "recipient_bank_name": payload.get("recipient_bank_name") or payload.get("bank_name"),
        "phone": payload.get("phone") or payload.get("recipient_phone") or payload.get("target_phone"),
        "network": payload.get("network"),
        "plan": payload.get("plan"),
        "source_bank_name": payload.get("source_bank_name"),
        "source_account_number": payload.get("source_account_number"),
        "narration": payload.get("narration") or payload.get("authored_narration") or payload.get("user_note"),
    }
    return {key: value for key, value in entry.items() if value not in (None, "", [])}


def _removed_task_from_entry(entry: Any) -> TaskSpec | None:
    task = entry.get("task") if isinstance(entry, dict) else None
    if isinstance(task, dict):
        task = TaskSpec.model_validate(task)
    return task if isinstance(task, TaskSpec) else None


def build_pending_action_edit_context(state: OrchestratorState, interrupt: Any) -> str:
    """Build compact context for semantic pending-action edit classification."""
    active_task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    active_entries = [
        _task_context_entry(task_id, task)
        for task_id in active_task_ids
        if (task := state.tasks.get(task_id)) is not None and task.type in _EDIT_TASK_TYPES
    ]
    removed_entries = []
    for task_id, entry in (state.removed_confirmation_tasks or {}).items():
        task = _removed_task_from_entry(entry)
        if task is None or task.type not in _EDIT_TASK_TYPES:
            continue
        removed_entries.append(_task_context_entry(str(task_id), task))

    lines = ["Active pending tasks:"]
    if active_entries:
        for entry in active_entries:
            lines.append(str(entry))
    else:
        lines.append("None")

    lines.append("Removed tasks available for restore:")
    if removed_entries:
        for entry in removed_entries:
            lines.append(str(entry))
    else:
        lines.append("None")
    return "\n".join(lines)


class PendingActionEditEngine:
    """Classify pending confirmation edits and prepare deterministic resolution hints."""

    async def interpret(
        self,
        *,
        state: OrchestratorState,
        interrupt: Any,
        text: str,
        task_planner: Any,
    ) -> PendingActionEditResolution | None:
        if getattr(interrupt, "kind", None) != "confirmation":
            return None
        if task_planner is None or not hasattr(task_planner, "interpret_pending_action_edit"):
            return None

        context = build_pending_action_edit_context(state, interrupt)
        decision = await task_planner.interpret_pending_action_edit(
            state.phone_number,
            text,
            context=context,
            path_label="interrupt_path",
        )
        if decision.confidence < PENDING_ACTION_EDIT_MIN_CONFIDENCE or decision.operation == "unclear":
            return None
        return PendingActionEditResolution(decision=decision)


__all__ = [
    "PendingActionEditEngine",
    "PendingActionEditResolution",
    "build_pending_action_edit_context",
]
