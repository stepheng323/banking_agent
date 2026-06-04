from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_details import (
    _format_task_details_for_status,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_requirements import (
    _build_requirements_hint,
    _friendly_required_field,
)


def _build_status_query_response(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_types: set[str],
    status_query_type: str | None,
) -> str:
    state_view = interrupt_state_view(state)
    flow_type = next(iter(sorted(task_types))) if task_types else "transaction"
    first_task = state_view.task(interrupt.task_ids[0]) if interrupt.task_ids else None
    required_fields = (
        list((interrupt.fields_by_task or {}).get(interrupt.task_ids[0], [])) if interrupt.task_ids else []
    )
    required_fields = [field for field in required_fields if isinstance(field, str)]
    status_kind = status_query_type or "recap"

    stage_text = {
        "input": "waiting for your input",
        "confirmation": "waiting for your confirmation",
        "auth": "waiting for your authorization",
    }.get(interrupt.kind, "in progress")

    if status_kind == "requirements":
        if required_fields:
            needed = ", ".join(_friendly_required_field(field) for field in required_fields)
            hint = _build_requirements_hint(required_fields, interrupt.kind)
            if hint:
                return f"I still need: {needed}. {hint}".strip()
            return f"I still need: {needed}."
        if interrupt.kind == "confirmation":
            return "I need your confirmation to continue. Reply yes to proceed or no to cancel."
        if interrupt.kind == "auth":
            return "I need PIN authorization to continue."
        return "I am waiting for your next input to continue."

    lines = [f"We are in your {flow_type} flow and currently {stage_text}."]
    if first_task:
        detail_line = _format_task_details_for_status(first_task, flow_type)
        if detail_line:
            lines.append(detail_line)
    if required_fields:
        needed = ", ".join(_friendly_required_field(field) for field in required_fields)
        hint = _build_requirements_hint(required_fields, interrupt.kind)
        if hint:
            lines.append(f"Next step: provide {needed}. {hint}")
        else:
            lines.append(f"Next step: provide {needed}.")
    elif interrupt.kind == "confirmation":
        lines.append("Next step: confirm to continue.")
    elif interrupt.kind == "auth":
        lines.append("Next step: complete authorization.")
    return " ".join(lines)


__all__ = ["_build_status_query_response"]
