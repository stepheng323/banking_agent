"""Observability helpers for TaskPlanner LLM calls."""

import time
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.observability.llm import ainvoke_with_config

StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)


def model_name(llm: Any) -> str | None:
    return cast(str | None, getattr(llm, "model_name", None) or getattr(llm, "model", None))


def log_latency_span(logger: Any, *, span: str, duration_ms: float, path_label: str) -> None:
    logger.info(
        "perf_timer_latency",
        gate=span,
        span=span,
        duration_ms=round(duration_ms, 2),
        path_label=path_label,
    )


async def invoke_structured_prompt(
    structured_llm: Any,
    response_type: type[StructuredResultT],
    *,
    system_prompt: str,
    user_prompt: str,
    logger: Any,
    event_name: str,
    model_llm: Any,
    path_label: str | None = None,
    latency_span: str | None = None,
    log_fields: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> StructuredResultT:
    start = time.perf_counter()
    try:
        result = await ainvoke_with_config(
            structured_llm,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            config=dict(config or {}) or None,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        emit_operational_event(
            "llm_structured_call_failed",
            severity="high",
            domain="llm",
            details={
                "event_name": event_name,
                "duration_ms": round(duration_ms, 2),
                "model": model_name(model_llm),
                "error_type": type(exc).__name__,
            },
        )
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    total_prompt_chars = len(system_prompt) + len(user_prompt)
    logger.info(
        event_name,
        duration_ms=round(duration_ms, 2),
        model=model_name(model_llm),
        system_chars=len(system_prompt),
        user_chars=len(user_prompt),
        **dict(log_fields or {}),
    )
    emit_operational_event(
        "llm_call_completed",
        severity="warning" if duration_ms > settings.llm_slow_call_threshold_ms else "info",
        domain="llm",
        details={
            "event_name": event_name,
            "duration_ms": round(duration_ms, 2),
            "model": model_name(model_llm),
            "prompt_chars": total_prompt_chars,
            "high_prompt_size": total_prompt_chars > settings.llm_high_prompt_size_chars,
        },
    )
    if total_prompt_chars > settings.llm_high_prompt_size_chars:
        emit_operational_event(
            "llm_high_prompt_size_warning",
            severity="warning",
            domain="llm",
            details={
                "event_name": event_name,
                "model": model_name(model_llm),
                "prompt_chars": total_prompt_chars,
            },
        )
    if path_label is not None and latency_span:
        log_latency_span(logger, span=latency_span, duration_ms=duration_ms, path_label=path_label)
    if isinstance(result, response_type):
        return result
    try:
        return cast(StructuredResultT, response_type.model_validate(result))
    except Exception as exc:
        emit_operational_event(
            "llm_structured_output_validation_failed",
            severity="high",
            domain="llm",
            details={
                "event_name": event_name,
                "model": model_name(model_llm),
                "response_type": response_type.__name__,
                "error_type": type(exc).__name__,
            },
        )
        raise
