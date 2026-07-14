"""Route and latency metrics for orchestrator graph turns."""

from collections import deque
from typing import Any

from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.models.turn_directive import TurnDirective, TurnNextStep
from shared.config.settings import settings
from shared.utils.logging import log_orchestrator_diagnostic


def _turn_directive(final_state: dict[str, Any]) -> TurnDirective | None:
    value = final_state.get("turn_directive")
    if isinstance(value, TurnDirective):
        return value
    if isinstance(value, dict):
        try:
            return TurnDirective.model_validate(value)
        except ValueError:
            return None
    return None


def resolve_path_label(context: MessageContext, final_state: dict[str, Any]) -> str:
    if getattr(context, "is_media_input", False) or bool(context.image_data):
        return "media_path"
    directive = _turn_directive(final_state)
    if directive is not None:
        if directive.next_step == TurnNextStep.HANDLE_INTERRUPT or directive.owner == "interrupt":
            return "interrupt_path"
        if directive.next_step == TurnNextStep.PLAN or directive.owner == "planner":
            return "planner_path"
        return "direct_path"
    # Pre-route and hydrated legacy states have no routing authority yet.
    return "planner_path"


def resolve_semantic_path_shape(
    context: MessageContext,
    final_state: dict[str, Any],
    path_label: str,
) -> str:
    if getattr(context, "is_media_input", False) or bool(context.image_data):
        return "media"
    directive = _turn_directive(final_state)
    if directive is not None:
        return directive.path_shape
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
    log_orchestrator_diagnostic(
        logger,
        "perf_timer_latency",
        gate=span,
        span=span,
        path_label=path_label,
        duration_ms=round(duration_ms, 2),
        phone_number=phone_number,
    )


def log_semantic_path_shape(logger: Any, *, semantic_path_shape: str, path_label: str, phone_number: str) -> None:
    log_orchestrator_diagnostic(
        logger,
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


def _pending_interrupt_kind(final_state: dict[str, Any]) -> str | None:
    pending_interrupt = final_state.get("pending_interrupt") or final_state.get("last_interrupt")
    if pending_interrupt is None:
        return None
    value = (
        pending_interrupt.get("kind")
        if isinstance(pending_interrupt, dict)
        else getattr(pending_interrupt, "kind", None)
    )
    return value if isinstance(value, str) and value else None


def _llm_total_ms(final_state: dict[str, Any]) -> float | None:
    value = final_state.get("llm_total_ms")
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    calls = final_state.get("llm_calls")
    if not isinstance(calls, list):
        return None

    total = 0.0
    has_duration = False
    for call in calls:
        duration = call.get("duration_ms") if isinstance(call, dict) else getattr(call, "duration_ms", None)
        if isinstance(duration, (int, float)):
            total += float(duration)
            has_duration = True
    return round(total, 2) if has_duration else None


def _llm_call_count(final_state: dict[str, Any]) -> int | None:
    calls = final_state.get("llm_calls")
    return len(calls) if isinstance(calls, list) else None


def log_turn_summary(
    logger: Any,
    *,
    final_state: dict[str, Any],
    path_label: str,
    total_duration_ms: float,
) -> None:
    task_map = final_state.get("tasks")
    task_count = len(task_map) if isinstance(task_map, dict) else 0
    directive = _turn_directive(final_state)
    logger.info(
        "orchestrator_turn_summary",
        total_ms=round(total_duration_ms, 2),
        llm_ms=_llm_total_ms(final_state),
        llm_call_count=_llm_call_count(final_state),
        path_label=path_label,
        routing_owner=directive.owner if directive else None,
        routing_decision=directive.decision if directive else None,
        interrupt_status=_pending_interrupt_kind(final_state),
        task_count=task_count,
    )


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
    directive = _turn_directive(final_state)
    logger.info(
        "orchestrator_route_metrics",
        phone_number=phone_number,
        path_label=path_label,
        semantic_path_shape=semantic_path_shape,
        routing_owner=directive.owner if directive else None,
        routing_decision=directive.decision if directive else None,
        routing_target_domain=directive.target_domain if directive else None,
        routing_mode=directive.mode if directive else None,
        planner_used=bool(final_state.get("planner_used")),
        planner_primary_intent=_planner_primary_intent(final_state),
        planner_clean=final_state.get("planner_clean"),
        planner_dirty_reasons=list(final_state.get("planner_dirty_reasons") or []),
        direct_path_triggered=path_label == "direct_path",
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
