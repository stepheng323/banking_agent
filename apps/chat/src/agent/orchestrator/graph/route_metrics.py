"""Route and latency metrics for orchestrator graph turns."""

from collections import deque
from typing import Any

from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from shared.config.settings import settings


def resolve_path_label(context: MessageContext, final_state: dict[str, Any]) -> str:
    if getattr(context, "is_media_input", False) or bool(context.image_data):
        return "media_path"
    if final_state.get("direct_path_triggered"):
        return "direct_path"
    if final_state.get("last_interrupt") or final_state.get("pending_interrupt"):
        return "interrupt_path"
    return "planner_path"


def resolve_semantic_path_shape(
    context: MessageContext,
    final_state: dict[str, Any],
    path_label: str,
) -> str:
    explicit = final_state.get("semantic_path_shape")
    if isinstance(explicit, str) and explicit:
        return explicit
    if getattr(context, "is_media_input", False) or bool(context.image_data):
        return "media"
    if path_label == "planner_path":
        return "planner"
    return path_label


def log_latency_span(
    logger: Any,
    *,
    span: str,
    duration_ms: float,
    phone_number: str,
    path_label: str,
) -> None:
    logger.info(
        "perf_timer_latency",
        gate=span,
        span=span,
        path_label=path_label,
        duration_ms=round(duration_ms, 2),
        phone_number=phone_number,
    )


def log_semantic_path_shape(logger: Any, *, semantic_path_shape: str, path_label: str, phone_number: str) -> None:
    logger.info(
        "orchestrator_semantic_path",
        semantic_path_shape=semantic_path_shape,
        path_label=path_label,
        phone_number=phone_number,
    )


def _task_executor_labels(final_state: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    tasks = final_state.get("tasks") or {}
    if not isinstance(tasks, dict):
        return labels
    for spec in tasks.values():
        executor = getattr(spec, "type", None)
        if isinstance(executor, str) and executor and executor not in labels:
            labels.append(executor)
    return labels


def _planner_primary_intent(final_state: dict[str, Any]) -> str | None:
    planner_output = final_state.get("planner_output")
    primary_intent = getattr(planner_output, "primary_intent", None)
    return primary_intent if isinstance(primary_intent, str) and primary_intent else None


def log_route_metrics(
    logger: Any,
    *,
    final_state: dict[str, Any],
    phone_number: str,
    path_label: str,
    semantic_path_shape: str,
    total_duration_ms: float,
    progress_count: int,
) -> None:
    task_executors = _task_executor_labels(final_state)
    task_map = final_state.get("tasks")
    task_count = len(task_map) if isinstance(task_map, dict) else len(task_executors)
    logger.info(
        "orchestrator_route_metrics",
        phone_number=phone_number,
        path_label=path_label,
        semantic_path_shape=semantic_path_shape,
        routing_owner=final_state.get("routing_owner"),
        routing_decision=final_state.get("routing_decision"),
        routing_target_domain=final_state.get("routing_target_domain"),
        routing_mode=final_state.get("routing_mode"),
        planner_used=bool(final_state.get("planner_used")),
        planner_primary_intent=_planner_primary_intent(final_state),
        direct_path_triggered=bool(final_state.get("direct_path_triggered")),
        expected_transaction_executors=list(final_state.get("preplanner_expected_transaction_executors") or []),
        task_executors=task_executors,
        task_count=task_count,
        wave_count=len(final_state.get("waves") or []),
        progress_count=progress_count,
        total_duration_ms=round(total_duration_ms, 2),
    )


def record_guardrail_signal(
    error_window: deque[int],
    logger: Any,
    *,
    path_label: str,
    duration_ms: float,
    errored: bool,
) -> None:
    error_window.append(1 if errored else 0)
    window_count = len(error_window)
    error_rate = (sum(error_window) / window_count) if window_count else 0.0
    breach_level = None
    if duration_ms > settings.latency_slo_p99_ms:
        breach_level = "p99"
    elif duration_ms > settings.latency_slo_p95_ms:
        breach_level = "p95"
    elif duration_ms > settings.latency_slo_p50_ms:
        breach_level = "p50"

    if breach_level:
        logger.warning(
            "latency_slo_breach",
            path_label=path_label,
            duration_ms=round(duration_ms, 2),
            threshold_ms=getattr(settings, f"latency_slo_{breach_level}_ms"),
            breach_level=breach_level,
        )
    if error_rate > settings.latency_slo_error_rate_threshold and window_count >= 20:
        logger.warning(
            "latency_rollback_signal",
            path_label=path_label,
            error_rate=round(error_rate, 4),
            threshold=settings.latency_slo_error_rate_threshold,
            recommendation="roll_back_recent_latency_changes",
        )
