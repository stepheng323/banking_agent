"""Observability helpers for TaskPlanner LLM calls."""

import time
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from pydantic import BaseModel

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
) -> StructuredResultT:
    start = time.perf_counter()
    result = await structured_llm.ainvoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        event_name,
        duration_ms=round(duration_ms, 2),
        model=model_name(model_llm),
        system_chars=len(system_prompt),
        user_chars=len(user_prompt),
        **dict(log_fields or {}),
    )
    if path_label is not None and latency_span:
        log_latency_span(logger, span=latency_span, duration_ms=duration_ms, path_label=path_label)
    if isinstance(result, response_type):
        return result
    return cast(StructuredResultT, response_type.model_validate(result))
