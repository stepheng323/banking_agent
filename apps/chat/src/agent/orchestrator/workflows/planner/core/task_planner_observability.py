"""Observability helpers for TaskPlanner LLM calls."""

import json
import time
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from shared.cache.llm_response_cache import (
    StructuredLLMResponseCache,
    prompt_hash,
    structured_response_cache_enabled,
)
from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.observability.llm import ainvoke_with_config
from shared.observability.llm_call_metrics import (
    estimated_tokens_from_chars,
    record_llm_call,
    role_from_event_name,
    structured_output_metrics,
)
from shared.observability.llm_http import (
    start_llm_http_recording,
    stop_llm_http_recording,
    summarize_llm_http_records,
)
from shared.observability.llm_provider_metadata import extract_provider_llm_metadata
from shared.utils.logging import log_orchestrator_diagnostic

StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)


def model_name(llm: Any) -> str | None:
    return cast(str | None, getattr(llm, "model_name", None) or getattr(llm, "model", None))


def log_latency_span(logger: Any, *, span: str, duration_ms: float, path_label: str) -> None:
    log_orchestrator_diagnostic(
        logger,
        "perf_timer_latency",
        gate=span,
        span=span,
        duration_ms=round(duration_ms, 2),
        path_label=path_label,
    )


def _role_from_event_name(event_name: str) -> str:
    return role_from_event_name(event_name)


def _output_record_fields(output_metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in output_metrics.items() if key not in {"output_json_chars", "output_token_estimate"}
    }


def _record_extra_fields(
    log_fields: Mapping[str, Any] | None,
    output_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    return {**dict(log_fields or {}), **_output_record_fields(output_metrics)}


def _provider_prompt_cache_fields(prompt_cache_key: str | None) -> dict[str, Any]:
    return {"provider_prompt_cache_key": prompt_cache_key} if prompt_cache_key else {}


def _prompt_cache_invocation_kwargs(prompt_cache_key: str | None) -> dict[str, Any]:
    return {"prompt_cache_key": prompt_cache_key} if prompt_cache_key else {}


def _split_raw_structured_result(result: Any) -> tuple[Any | None, Any, Any | None]:
    if isinstance(result, Mapping) and {"raw", "parsed", "parsing_error"}.issubset(result.keys()):
        return result.get("raw"), result.get("parsed"), result.get("parsing_error")
    return None, result, None


def _raise_parsing_error(error: Any) -> None:
    if isinstance(error, BaseException):
        raise error
    raise RuntimeError(str(error))


def _find_discriminators(value: Any) -> list[dict[str, Any]]:
    discriminators: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        raw_discriminator = value.get("discriminator")
        if isinstance(raw_discriminator, Mapping):
            field = raw_discriminator.get("propertyName")
            mapping = raw_discriminator.get("mapping")
            discriminators.append(
                {
                    "field": str(field or ""),
                    "mapping_count": len(mapping) if isinstance(mapping, Mapping) else 0,
                }
            )
        for item in value.values():
            discriminators.extend(_find_discriminators(item))
    elif isinstance(value, list):
        for item in value:
            discriminators.extend(_find_discriminators(item))
    return discriminators


def _count_action_const_variants(value: Any) -> int:
    if isinstance(value, Mapping):
        count = 0
        const_value = value.get("const")
        if isinstance(const_value, str):
            count += 1 if value.get("title") == "Action" or const_value else 0
        return count + sum(_count_action_const_variants(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_action_const_variants(item) for item in value)
    return 0


@lru_cache(maxsize=64)
def _response_schema_metrics(response_type: type[BaseModel]) -> dict[str, Any]:
    try:
        schema = response_type.model_json_schema()
        schema_json = json.dumps(schema, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, AttributeError):
        return {}

    defs = schema.get("$defs") or schema.get("definitions") or {}
    discriminators = _find_discriminators(schema)
    action_discriminators = [item for item in discriminators if item.get("field") == "action"]
    action_variant_count = max([int(item.get("mapping_count") or 0) for item in action_discriminators] or [0])
    if action_variant_count == 0:
        action_variant_count = _count_action_const_variants(schema)

    return {
        "response_schema_json_chars": len(schema_json),
        "response_schema_token_estimate": estimated_tokens_from_chars(len(schema_json)),
        "response_schema_defs_count": len(defs) if isinstance(defs, Mapping) else 0,
        "response_schema_discriminator_count": len(discriminators),
        "response_schema_discriminator_fields": sorted(
            {str(item.get("field") or "") for item in discriminators if item.get("field")}
        ),
        "response_schema_action_variant_count": action_variant_count,
    }


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
    prompt_cache_key: str | None = None,
) -> StructuredResultT:
    start = time.perf_counter()
    response_type_name = response_type.__name__
    schema_metrics = _response_schema_metrics(response_type)
    llm_model_name = model_name(model_llm)
    system_prompt_hash = prompt_hash(system_prompt)
    user_prompt_hash = prompt_hash(user_prompt)
    total_prompt_chars = len(system_prompt) + len(user_prompt)
    prompt_cache_fields = _provider_prompt_cache_fields(prompt_cache_key)
    prompt_cache_invocation_kwargs = _prompt_cache_invocation_kwargs(prompt_cache_key)
    cache_allowed = structured_response_cache_enabled(response_type)
    cache_status = "disabled"
    if cache_allowed:
        cached = await StructuredLLMResponseCache().get(
            response_type=response_type,
            model=llm_model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        if cached is not None:
            duration_ms = (time.perf_counter() - start) * 1000
            output_metrics = structured_output_metrics(cached)
            logger.info(
                event_name,
                duration_ms=round(duration_ms, 2),
                model=llm_model_name,
                system_chars=len(system_prompt),
                user_chars=len(user_prompt),
                prompt_token_estimate=estimated_tokens_from_chars(total_prompt_chars),
                response_type=response_type_name,
                cache_status="hit",
                system_prompt_hash=system_prompt_hash,
                user_prompt_hash=user_prompt_hash,
                **schema_metrics,
                **output_metrics,
                **dict(log_fields or {}),
            )
            record_llm_call(
                event_name=event_name,
                duration_ms=duration_ms,
                model=llm_model_name,
                response_type=response_type_name,
                system_chars=len(system_prompt),
                user_chars=len(user_prompt),
                cache_status="hit",
                path_label=path_label,
                latency_span=latency_span,
                output_json_chars=int(output_metrics["output_json_chars"]),
                output_token_estimate=int(output_metrics["output_token_estimate"]),
                extra_fields=_record_extra_fields({**schema_metrics, **dict(log_fields or {})}, output_metrics),
            )
            emit_operational_event(
                "llm_response_cache_hit",
                severity="info",
                domain="llm",
                details={
                    "event_name": event_name,
                    "response_type": response_type_name,
                    "model": llm_model_name,
                    "system_prompt_hash": system_prompt_hash,
                    "user_prompt_hash": user_prompt_hash,
                },
                logger=logger,
            )
            if path_label is not None and latency_span:
                log_latency_span(logger, span=latency_span, duration_ms=duration_ms, path_label=path_label)
            return cached
        cache_status = "miss"
    http_recording_token = start_llm_http_recording()
    try:
        result = await ainvoke_with_config(
            structured_llm,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            config=dict(config or {}) or None,
            invocation_kwargs=prompt_cache_invocation_kwargs,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        http_metrics = summarize_llm_http_records(stop_llm_http_recording(http_recording_token))
        record_llm_call(
            event_name=event_name,
            duration_ms=duration_ms,
            model=llm_model_name,
            response_type=response_type_name,
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            cache_status=cache_status,
            path_label=path_label,
            latency_span=latency_span,
            extra_fields={**schema_metrics, **prompt_cache_fields, **http_metrics, **dict(log_fields or {})},
            error_type=type(exc).__name__,
        )
        emit_operational_event(
            "llm_structured_call_failed",
            severity="high",
            domain="llm",
            details={
                "event_name": event_name,
                "duration_ms": round(duration_ms, 2),
                "model": llm_model_name,
                "response_type": response_type_name,
                "system_prompt_hash": system_prompt_hash,
                "user_prompt_hash": user_prompt_hash,
                "error_type": type(exc).__name__,
            },
            logger=logger,
        )
        raise
    http_metrics = summarize_llm_http_records(stop_llm_http_recording(http_recording_token))
    duration_ms = (time.perf_counter() - start) * 1000
    raw_response, parsed_result, parsing_error = _split_raw_structured_result(result)
    provider_metadata = extract_provider_llm_metadata(raw_response) if raw_response is not None else {}
    provider_fields = {**prompt_cache_fields, **http_metrics, **provider_metadata}
    if parsing_error is not None:
        record_llm_call(
            event_name=event_name,
            duration_ms=duration_ms,
            model=llm_model_name,
            response_type=response_type_name,
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            cache_status=cache_status,
            path_label=path_label,
            latency_span=latency_span,
            extra_fields={**schema_metrics, **provider_fields, **dict(log_fields or {})},
            error_type=type(parsing_error).__name__,
        )
        emit_operational_event(
            "llm_structured_output_validation_failed",
            severity="high",
            domain="llm",
            details={
                "event_name": event_name,
                "model": llm_model_name,
                "response_type": response_type_name,
                "error_type": type(parsing_error).__name__,
                **provider_fields,
            },
            logger=logger,
        )
        _raise_parsing_error(parsing_error)
    if isinstance(parsed_result, response_type):
        validated = parsed_result
    else:
        try:
            validated = cast(StructuredResultT, response_type.model_validate(parsed_result))
        except Exception as exc:
            record_llm_call(
                event_name=event_name,
                duration_ms=duration_ms,
                model=llm_model_name,
                response_type=response_type_name,
                system_chars=len(system_prompt),
                user_chars=len(user_prompt),
                cache_status=cache_status,
                path_label=path_label,
                latency_span=latency_span,
                extra_fields={**schema_metrics, **provider_fields, **dict(log_fields or {})},
                error_type=type(exc).__name__,
            )
            emit_operational_event(
                "llm_structured_output_validation_failed",
                severity="high",
                domain="llm",
                details={
                    "event_name": event_name,
                    "model": llm_model_name,
                    "response_type": response_type_name,
                    "error_type": type(exc).__name__,
                    **provider_fields,
                },
                logger=logger,
            )
            raise
    output_metrics = structured_output_metrics(validated)
    logger.info(
        event_name,
        duration_ms=round(duration_ms, 2),
        model=llm_model_name,
        system_chars=len(system_prompt),
        user_chars=len(user_prompt),
        response_type=response_type_name,
        cache_status=cache_status,
        system_prompt_hash=system_prompt_hash,
        user_prompt_hash=user_prompt_hash,
        prompt_token_estimate=estimated_tokens_from_chars(total_prompt_chars),
        **provider_fields,
        **schema_metrics,
        **output_metrics,
        **dict(log_fields or {}),
    )
    record_llm_call(
        event_name=event_name,
        duration_ms=duration_ms,
        model=llm_model_name,
        response_type=response_type_name,
        system_chars=len(system_prompt),
        user_chars=len(user_prompt),
        cache_status=cache_status,
        path_label=path_label,
        latency_span=latency_span,
        output_json_chars=int(output_metrics["output_json_chars"]),
        output_token_estimate=int(output_metrics["output_token_estimate"]),
        extra_fields=_record_extra_fields(
            {**schema_metrics, **provider_fields, **dict(log_fields or {})},
            output_metrics,
        ),
    )
    emit_operational_event(
        "llm_call_completed",
        severity="warning" if duration_ms > settings.llm_slow_call_threshold_ms else "info",
        domain="llm",
        details={
            "event_name": event_name,
            "duration_ms": round(duration_ms, 2),
            "model": llm_model_name,
            "llm_role": _role_from_event_name(event_name),
            "response_type": response_type_name,
            "cache_status": cache_status,
            "prompt_chars": total_prompt_chars,
            "prompt_token_estimate": estimated_tokens_from_chars(total_prompt_chars),
            **provider_fields,
            **schema_metrics,
            **output_metrics,
            "system_prompt_hash": system_prompt_hash,
            "user_prompt_hash": user_prompt_hash,
            "high_prompt_size": total_prompt_chars > settings.llm_high_prompt_size_chars,
        },
        logger=logger,
    )
    emit_operational_event(
        "llm_call_counter",
        severity="info",
        domain="llm",
        details={
            "event_name": event_name,
            "llm_role": _role_from_event_name(event_name),
            "model": llm_model_name,
            "response_type": response_type_name,
            "cache_status": cache_status,
        },
        logger=logger,
    )
    if total_prompt_chars > settings.llm_high_prompt_size_chars:
        emit_operational_event(
            "llm_high_prompt_size_warning",
            severity="warning",
            domain="llm",
            details={
                "event_name": event_name,
                "model": llm_model_name,
                "response_type": response_type_name,
                "prompt_chars": total_prompt_chars,
            },
            logger=logger,
        )
    if path_label is not None and latency_span:
        log_latency_span(logger, span=latency_span, duration_ms=duration_ms, path_label=path_label)
    if cache_allowed:
        await StructuredLLMResponseCache().set(
            response=validated,
            model=llm_model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        emit_operational_event(
            "llm_response_cache_miss",
            severity="info",
            domain="llm",
            details={
                "event_name": event_name,
                "response_type": response_type_name,
                "model": llm_model_name,
                "system_prompt_hash": system_prompt_hash,
                "user_prompt_hash": user_prompt_hash,
            },
            logger=logger,
        )
    return validated
