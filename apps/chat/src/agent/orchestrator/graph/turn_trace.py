"""Log-only orchestrator turn trace summaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import TurnDirective
from shared.utils.logging import log_orchestrator_diagnostic


def _pending_interrupt_kind(final_state: Mapping[str, Any]) -> str | None:
    interrupt = final_state.get("pending_interrupt")
    if interrupt is None:
        return None
    value = interrupt.get("kind") if isinstance(interrupt, Mapping) else getattr(interrupt, "kind", None)
    return value if isinstance(value, str) and value else None


def _task_executors(final_state: Mapping[str, Any]) -> list[str]:
    raw_tasks = final_state.get("tasks")
    if not isinstance(raw_tasks, Mapping):
        return []

    executors: set[str] = set()
    for task in raw_tasks.values():
        value = task.get("type") if isinstance(task, Mapping) else getattr(task, "type", None)
        if isinstance(value, str) and value:
            executors.add(value)
    return sorted(executors)


def _wave_count(final_state: Mapping[str, Any]) -> int:
    waves = final_state.get("waves")
    return len(waves) if isinstance(waves, list) else 0


def _turn_directive(final_state: Mapping[str, Any]) -> TurnDirective | None:
    value = final_state.get("turn_directive")
    if isinstance(value, TurnDirective):
        return value
    if isinstance(value, Mapping):
        try:
            return TurnDirective.model_validate(value)
        except ValueError:
            return None
    return None


def summarize_orchestrator_turn_trace(
    *,
    final_state: Mapping[str, Any],
    path_label: str,
    semantic_path_shape: str,
    total_duration_ms: float,
    progress_count: int,
) -> dict[str, object]:
    directive = _turn_directive(final_state)
    return {
        "path_label": path_label,
        "semantic_path_shape": semantic_path_shape,
        "gate_match": directive.decision if directive else None,
        "planner_used": bool(final_state.get("planner_used")),
        "execution_wave_count": _wave_count(final_state),
        "interrupt_status": _pending_interrupt_kind(final_state),
        "task_executors": _task_executors(final_state),
        "routing_owner": directive.owner if directive else None,
        "routing_decision": directive.decision if directive else None,
        "routing_target_domain": directive.target_domain if directive else None,
        "routing_mode": directive.mode if directive else None,
        "route_source": directive.source if directive else None,
        "progress_count": progress_count,
        "total_duration_ms": round(total_duration_ms, 3),
    }


def log_orchestrator_turn_trace(
    logger: Any,
    *,
    final_state: Mapping[str, Any],
    path_label: str,
    semantic_path_shape: str,
    total_duration_ms: float,
    progress_count: int,
) -> None:
    log_orchestrator_diagnostic(
        logger,
        "orchestrator_turn_trace",
        **summarize_orchestrator_turn_trace(
            final_state=final_state,
            path_label=path_label,
            semantic_path_shape=semantic_path_shape,
            total_duration_ms=total_duration_ms,
            progress_count=progress_count,
        ),
    )


__all__ = ["log_orchestrator_turn_trace", "summarize_orchestrator_turn_trace"]
