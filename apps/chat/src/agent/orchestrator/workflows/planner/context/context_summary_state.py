"""Turn-context summary state payload and cache helpers."""

import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_types import TurnContextSummary


def summary_to_state_payload(summary: TurnContextSummary) -> dict[str, Any]:
    return asdict(summary)


def summary_from_state_payload(payload: Any) -> TurnContextSummary | None:
    if isinstance(payload, TurnContextSummary):
        return payload
    if isinstance(payload, dict):
        return TurnContextSummary(**payload)
    return None


def _get_or_build_turn_context_summary(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None = None,
    query_session_source: str | None = None,
    path_label: str | None = None,
    builder: Callable[..., TurnContextSummary],
    perf_logger: Any,
) -> tuple[TurnContextSummary, dict[str, Any] | None]:
    cached = summary_from_state_payload(state.turn_context_summary)
    if cached is not None:
        return cached, None

    build_start = time.perf_counter()
    summary = builder(
        state,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
    )
    if path_label:
        perf_logger.info(
            "perf_timer_latency",
            gate="turn_context_summary_build",
            span="turn_context_summary_build",
            duration_ms=round((time.perf_counter() - build_start) * 1000, 2),
            path_label=path_label,
            phone_number=state.phone_number,
        )
    return summary, {"turn_context_summary": summary_to_state_payload(summary)}


__all__ = [
    "_get_or_build_turn_context_summary",
    "summary_from_state_payload",
    "summary_to_state_payload",
]
