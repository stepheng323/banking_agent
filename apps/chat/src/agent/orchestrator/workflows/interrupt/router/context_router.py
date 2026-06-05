import json

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.context_router_payloads import (
    _build_active_task_router_state,
    _compact_task_payload_for_interrupt_router,
    _minimal_task_payload_for_interrupt_router,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.context_router_prompt import (
    _clip_text,
    _select_interrupt_router_prompt_mode,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS,
    INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS,
    INTERRUPT_PROMPT_COMPACT_MAX_CHARS,
    INTERRUPT_PROMPT_MAX_CHARS,
    INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS,
    INTERRUPT_REQUIRED_FIELDS_MAX_CHARS,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_active import (
    build_interrupt_context_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_core import (
    INTERRUPT_CONTEXT_MAX_CHARS,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary import (
    get_or_build_turn_context_summary,
)
from shared.utils.logging import get_logger

logger = get_logger("apps.chat.src.agent.orchestrator.workflows.interrupt")


def _build_interrupt_context_details(
    *,
    state: OrchestratorState,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> tuple[str, str, str]:
    prompt_mode = _select_interrupt_router_prompt_mode(
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
    )
    compact_mode = prompt_mode == "compact"
    state_view = interrupt_state_view(state)
    summary, _ = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=state_view.stashed_query_session,
        query_session_source="stashed" if state_view.stashed_query_session is not None else None,
        path_label="interrupt_path",
    )
    active_task_state = _build_active_task_router_state(
        state=state,
        task_ids=task_ids,
        compact_mode=compact_mode,
    )
    raw_active_task_state_text = json.dumps(active_task_state, ensure_ascii=True)
    active_task_state_text = _clip_text(
        raw_active_task_state_text,
        INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS if compact_mode else INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS,
    )
    raw_required_fields_text = json.dumps(fields_by_task, ensure_ascii=True)
    required_fields_text = _clip_text(
        raw_required_fields_text,
        INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS if compact_mode else INTERRUPT_REQUIRED_FIELDS_MAX_CHARS,
    )
    raw_prompt_text = prompt or ""
    prompt_text = _clip_text(
        raw_prompt_text,
        INTERRUPT_PROMPT_COMPACT_MAX_CHARS if compact_mode else INTERRUPT_PROMPT_MAX_CHARS,
    )
    context = build_interrupt_context_from_summary(
        summary,
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
        active_task_state_json=active_task_state_text,
        required_fields_json=required_fields_text,
        prompt_text=prompt_text,
        prompt_mode=prompt_mode,
    )
    logger.info(
        "interrupt_context_size",
        chars=len(context),
        final_chars=len(context),
        raw_active_task_state_chars=len(raw_active_task_state_text),
        clipped_active_task_state_chars=len(active_task_state_text),
        raw_required_fields_chars=len(raw_required_fields_text),
        clipped_required_fields_chars=len(required_fields_text),
        raw_prompt_chars=len(raw_prompt_text),
        clipped_prompt_chars=len(prompt_text),
        truncated=len(context) >= INTERRUPT_CONTEXT_MAX_CHARS,
        prompt_mode=prompt_mode,
        active_task_state_mode="minimal" if compact_mode else "json",
        task_count=len(task_ids),
        interrupt_kind=kind,
    )
    return context, prompt_mode, ("minimal" if compact_mode else "json")


def _build_interrupt_context(
    *,
    state: OrchestratorState,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> str:
    context, _, _ = _build_interrupt_context_details(
        state=state,
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
        fields_by_task=fields_by_task,
        prompt=prompt,
    )
    return context


__all__ = [
    "_build_active_task_router_state",
    "_build_interrupt_context",
    "_build_interrupt_context_details",
    "_clip_text",
    "_compact_task_payload_for_interrupt_router",
    "_minimal_task_payload_for_interrupt_router",
    "_select_interrupt_router_prompt_mode",
]
