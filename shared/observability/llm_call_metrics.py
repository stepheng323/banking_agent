"""In-process LLM call metrics for readiness and local diagnostics."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

from pydantic import BaseModel

from shared.utils.json import to_json_safe

LLMCallRecord = dict[str, Any]

_LLM_CALL_RECORDS: ContextVar[list[LLMCallRecord] | None] = ContextVar("llm_call_records", default=None)
_PROCESS_START = time.perf_counter()
_PROCESS_LLM_CALL_COUNT = 0
_PROCESS_LLM_EVENT_COUNTS: dict[str, int] = {}


def estimated_tokens_from_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return max(1, round(char_count / 4))


def role_from_event_name(event_name: str) -> str:
    if event_name.endswith("_llm_call"):
        return event_name[: -len("_llm_call")]
    return event_name


def structured_output_metrics(result: Any) -> dict[str, Any]:
    expanded_data: Any
    compact_data: Any
    expanded_json: str
    compact_json: str
    if isinstance(result, BaseModel):
        expanded_json = result.model_dump_json()
        expanded_data = result.model_dump(mode="json")
        compact_json = result.model_dump_json(exclude_none=True, exclude_defaults=True, exclude_unset=True)
        compact_data = result.model_dump(mode="json", exclude_none=True, exclude_defaults=True, exclude_unset=True)
    else:
        try:
            expanded_data = to_json_safe(result)
            expanded_json = json.dumps(expanded_data, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            expanded_json = str(result)
            expanded_data = expanded_json
        compact_json = expanded_json
        compact_data = expanded_data
    expanded_json_chars = len(expanded_json)
    compact_json_chars = len(compact_json)
    metrics: dict[str, Any] = {
        "output_json_chars": compact_json_chars,
        "output_token_estimate": estimated_tokens_from_chars(compact_json_chars),
        "output_compact_json_chars": compact_json_chars,
        "output_compact_token_estimate": estimated_tokens_from_chars(compact_json_chars),
        "output_expanded_json_chars": expanded_json_chars,
        "output_expanded_token_estimate": estimated_tokens_from_chars(expanded_json_chars),
        "output_default_overhead_chars": max(0, expanded_json_chars - compact_json_chars),
        "output_null_field_count": _count_null_fields(expanded_data),
        "output_empty_field_count": _count_empty_fields(expanded_data),
        "output_top_field_chars": _top_field_chars(compact_data),
        "output_expanded_top_field_chars": _top_field_chars(expanded_data),
        "output_compact_top_field_chars": _top_field_chars(compact_data),
    }
    if isinstance(result, BaseModel):
        metrics["output_set_field_count"] = len(result.model_fields_set)
    metrics.update(
        _planner_output_shape_metrics(
            expanded_data,
            compact_data,
            source_model=result if isinstance(result, BaseModel) else None,
        )
    )
    return metrics


def response_schema_metrics(response_type: type[BaseModel]) -> dict[str, int]:
    """Return compact schema-size metrics shared by non-planner LLM roles."""
    try:
        schema_json = json.dumps(response_type.model_json_schema(), separators=(",", ":"), sort_keys=True)
    except (AttributeError, TypeError, ValueError):
        return {}
    return {
        "response_schema_json_chars": len(schema_json),
        "response_schema_token_estimate": estimated_tokens_from_chars(len(schema_json)),
    }


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(to_json_safe(value), separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError):
        return len(str(value))


def _count_null_fields(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, Mapping):
        return sum(_count_null_fields(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_null_fields(item) for item in value)
    return 0


def _count_empty_fields(value: Any) -> int:
    if value == "" or value == [] or value == {}:
        return 1
    if isinstance(value, Mapping):
        return sum(_count_empty_fields(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_empty_fields(item) for item in value)
    return 0


def _top_field_chars(value: Any, *, limit: int = 8) -> list[dict[str, int | str]]:
    if not isinstance(value, Mapping):
        return []
    fields: list[dict[str, int | str]] = [
        {"field": str(key), "chars": _json_chars(item)} for key, item in value.items()
    ]
    fields.sort(key=lambda item: item["chars"] if isinstance(item["chars"], int) else 0, reverse=True)
    return fields[:limit]


def _active_parameter_keys(parameters: Any) -> list[str]:
    if not isinstance(parameters, Mapping):
        return []
    return sorted(str(key) for key, value in parameters.items() if value not in (None, "", [], {}, False))


def _set_field_keys(model: Any) -> list[str]:
    if not isinstance(model, BaseModel):
        return []
    return sorted(str(key) for key in model.model_fields_set)


def _planner_task_models(source_model: BaseModel | None) -> list[Any]:
    if source_model is None:
        return []
    tasks = getattr(source_model, "tasks", None)
    return tasks if isinstance(tasks, list) else []


def _planner_output_shape_metrics(
    output_data: Any,
    compact_data: Any,
    *,
    source_model: BaseModel | None,
) -> dict[str, Any]:
    if not isinstance(output_data, Mapping):
        return {}
    tasks = output_data.get("tasks")
    if not isinstance(tasks, list):
        return {}
    compact_tasks_raw = compact_data.get("tasks") if isinstance(compact_data, Mapping) else []
    compact_tasks = compact_tasks_raw if isinstance(compact_tasks_raw, list) else []
    task_models = _planner_task_models(source_model)

    task_shapes: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, Mapping):
            continue
        compact_task = compact_tasks[index - 1] if index <= len(compact_tasks) else {}
        compact_task_mapping = compact_task if isinstance(compact_task, Mapping) else {}
        parameters = task.get("parameters")
        compact_parameters = compact_task_mapping.get("parameters")
        task_model = task_models[index - 1] if index <= len(task_models) else None
        parameter_model = getattr(task_model, "parameters", None) if isinstance(task_model, BaseModel) else None
        executor = getattr(task_model, "executor", None) if isinstance(task_model, BaseModel) else task.get("executor")
        parameter_set_keys = _set_field_keys(parameter_model)
        parameter_null_set_keys = [
            key for key in parameter_set_keys if isinstance(parameters, Mapping) and parameters.get(key) is None
        ]
        task_shapes.append(
            {
                "index": index,
                "executor": str(executor or ""),
                "action": str(task.get("action") or ""),
                "json_chars": _json_chars(task),
                "compact_json_chars": _json_chars(compact_task_mapping) if compact_task_mapping else 0,
                "parameter_key_count": len(parameters) if isinstance(parameters, Mapping) else 0,
                "parameter_set_field_count": len(parameter_set_keys),
                "parameter_null_set_count": len(parameter_null_set_keys),
                "parameter_set_keys": parameter_set_keys,
                "active_parameter_key_count": len(_active_parameter_keys(compact_parameters)),
                "active_parameter_keys": _active_parameter_keys(compact_parameters),
            }
        )
    return {
        "planner_task_count": len(tasks),
        "planner_task_shapes": task_shapes,
    }


def start_llm_call_recording() -> Token[list[LLMCallRecord] | None]:
    return _LLM_CALL_RECORDS.set([])


def stop_llm_call_recording(token: Token[list[LLMCallRecord] | None]) -> tuple[LLMCallRecord, ...]:
    records = tuple(_LLM_CALL_RECORDS.get() or ())
    _LLM_CALL_RECORDS.reset(token)
    # Invocation scopes can be nested: the graph captures its own detailed
    # per-turn calls while readiness wraps the full public invocation.  Keep
    # the inner snapshot for the graph response, but also aggregate it into
    # the parent scope so an outer diagnostic does not silently report zero
    # calls.
    parent_records = _LLM_CALL_RECORDS.get()
    if parent_records is not None:
        parent_records.extend(records)
    return records


def get_recorded_llm_calls() -> tuple[LLMCallRecord, ...]:
    return tuple(_LLM_CALL_RECORDS.get() or ())


def record_llm_call(
    *,
    event_name: str,
    duration_ms: float,
    model: str | None,
    response_type: str | None,
    system_chars: int,
    user_chars: int,
    path_label: str | None = None,
    latency_span: str | None = None,
    output_json_chars: int | None = None,
    output_token_estimate: int | None = None,
    extra_fields: Mapping[str, Any] | None = None,
    error_type: str | None = None,
) -> None:
    global _PROCESS_LLM_CALL_COUNT

    records = _LLM_CALL_RECORDS.get()
    if records is None:
        return

    prompt_chars = system_chars + user_chars
    call_index = len(records) + 1
    event_call_index = sum(1 for record in records if record.get("event_name") == event_name) + 1
    _PROCESS_LLM_CALL_COUNT += 1
    _PROCESS_LLM_EVENT_COUNTS[event_name] = _PROCESS_LLM_EVENT_COUNTS.get(event_name, 0) + 1
    record: LLMCallRecord = {
        "llm_call_index": call_index,
        "llm_event_call_index": event_call_index,
        "process_llm_call_index": _PROCESS_LLM_CALL_COUNT,
        "process_llm_event_call_index": _PROCESS_LLM_EVENT_COUNTS[event_name],
        "process_uptime_ms": round((time.perf_counter() - _PROCESS_START) * 1000, 2),
        "event_name": event_name,
        "llm_role": role_from_event_name(event_name),
        "duration_ms": round(duration_ms, 2),
        "model": model,
        "response_type": response_type,
        "system_chars": system_chars,
        "user_chars": user_chars,
        "prompt_chars": prompt_chars,
        "prompt_token_estimate": estimated_tokens_from_chars(prompt_chars),
        "output_json_chars": output_json_chars,
        "output_token_estimate": output_token_estimate,
        "path_label": path_label,
        "latency_span": latency_span,
    }
    from shared.config.settings import settings

    deadline_seconds = settings.llm_deadline_seconds(role_from_event_name(event_name))
    if deadline_seconds is not None:
        record["deadline_seconds"] = deadline_seconds
        record["deadline_outcome"] = "exceeded" if error_type == "LLMCallDeadlineExceeded" else "within"
    if error_type:
        record["error_type"] = error_type
    for key, value in (extra_fields or {}).items():
        record[key] = _record_safe_value(value)
    records.append({key: value for key, value in record.items() if value is not None})


def _record_safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_record_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _record_safe_value(item) for key, item in value.items()}
    try:
        return to_json_safe(value)
    except (TypeError, ValueError):
        return str(value)
