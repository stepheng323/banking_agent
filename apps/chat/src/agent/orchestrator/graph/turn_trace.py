"""Log-only orchestrator turn trace summaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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


def summarize_orchestrator_turn_trace(
    *,
    final_state: Mapping[str, Any],
    path_label: str,
    semantic_path_shape: str,
    total_duration_ms: float,
    progress_count: int,
) -> dict[str, object]:
    return {
        "path_label": path_label,
        "semantic_path_shape": semantic_path_shape,
        "gate_match": final_state.get("routing_decision"),
        "planner_used": bool(final_state.get("planner_used")),
        "execution_wave_count": _wave_count(final_state),
        "interrupt_status": _pending_interrupt_kind(final_state),
        "task_executors": _task_executors(final_state),
        "routing_owner": final_state.get("routing_owner"),
        "routing_decision": final_state.get("routing_decision"),
        "routing_target_domain": final_state.get("routing_target_domain"),
        "routing_mode": final_state.get("routing_mode"),
        "route_source": final_state.get("route_source"),
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
