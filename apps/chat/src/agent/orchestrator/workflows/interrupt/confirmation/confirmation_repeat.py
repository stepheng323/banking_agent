"""Deterministic repeat detection for confirmation interrupts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_text import (
    _normalize_recipient_match_text,
)
from shared.types.planner import InterruptRouteDecision
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_deterministic_confirmation_repeat_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    if getattr(interrupt, "kind", None) != "confirmation":
        return None

    normalized_text = _normalize_recipient_match_text(text)
    if not normalized_text:
        return None

    matched_task_ids: list[str] = []
    for task_id in getattr(interrupt, "task_ids", []) or []:
        task = state.tasks.get(str(task_id))
        if task is None:
            continue
        if normalized_text in _task_request_variants(task):
            matched_task_ids.append(str(task_id))

    if not matched_task_ids:
        return None

    logger.info(
        "interrupt_repeat_in_flow_detected",
        kind=interrupt.kind,
        matched_task_ids=matched_task_ids,
        match_kind="exact_request_repeat",
    )
    return InterruptRouteDecision(
        decision="continue_flow",
        confidence=0.99,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason="shortcut_confirmation_exact_repeat",
    )


def _task_request_variants(task: TaskSpec) -> list[str]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    variants: list[str] = []
    for field in ("message", "instruction"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            variants.append(_normalize_recipient_match_text(value))
    return [variant for variant in variants if variant]


__all__ = [
    "_resolve_deterministic_confirmation_repeat_route",
    "_task_request_variants",
]
