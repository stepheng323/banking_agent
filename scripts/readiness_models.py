"""Data models for readiness transcript runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReadinessMode = Literal["deterministic", "dry-run"]
ReadinessScenarioName = Literal[
    "all",
    "core",
    "transfer",
    "data",
    "airtime",
    "faq",
    "unsupported",
    "schedule",
    "quick",
    "mvp",
    "query",
    "query-deep",
    "latency",
    "llm-latency",
    "planner",
]


@dataclass(frozen=True)
class ReadinessExpectation:
    expect_any: tuple[str, ...] = ()
    expect_all: tuple[str, ...] = ()
    expect_none: tuple[str, ...] = ()
    expect_path_shape: str | None = None
    expect_routing_owner: str | None = None
    expect_routing_decision: str | None = None
    expect_task_types: tuple[str, ...] | None = None
    expect_async_job_count_delta: int | None = None
    expect_async_job_topics: tuple[str, ...] | None = None
    expect_planner_clean: bool | None = None
    expect_llm_call_count: int | None = None
    expect_llm_event_counts: tuple[tuple[str, int], ...] = ()
    allow_duplicate_blocks: bool = False


@dataclass(frozen=True)
class ReadinessTurn:
    text: str
    expectation: ReadinessExpectation = field(default_factory=ReadinessExpectation)
    pin_after: bool = False
    pin_flow_type: str = "transaction"
    modes: tuple[ReadinessMode, ...] = ("deterministic", "dry-run")


@dataclass(frozen=True)
class ReadinessScenario:
    id: str
    turns: tuple[ReadinessTurn, ...]
    description: str = ""


@dataclass(frozen=True)
class ReadinessInvocation:
    response: dict[str, Any]
    route_metadata: dict[str, Any] = field(default_factory=dict)
    task_types: tuple[str, ...] = ()
    async_jobs: tuple[dict[str, Any], ...] = ()
    llm_calls: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReadinessTurnResult:
    scenario_id: str
    turn_index: int
    user_text: str
    response_text: str
    latency_ms: float
    passed: bool
    errors: tuple[str, ...] = ()
    route_metadata: dict[str, Any] = field(default_factory=dict)
    task_types: tuple[str, ...] = ()
    async_jobs: tuple[dict[str, Any], ...] = ()
    llm_calls: tuple[dict[str, Any], ...] = ()
    planner_clean: bool | None = None
    planner_dirty_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "turn_index": self.turn_index,
            "user_text": self.user_text,
            "response_text": self.response_text,
            "latency_ms": round(self.latency_ms, 2),
            "passed": self.passed,
            "errors": list(self.errors),
            "route_metadata": self.route_metadata,
            "task_types": list(self.task_types),
            "async_jobs": list(self.async_jobs),
            "llm_calls": list(self.llm_calls),
            "llm_total_ms": round(_llm_call_total_ms(self.llm_calls), 2),
            "planner_clean": self.planner_clean,
            "planner_dirty_reasons": list(self.planner_dirty_reasons),
        }


@dataclass(frozen=True)
class ReadinessRunResult:
    mode: ReadinessMode
    scenario_ids: tuple[str, ...]
    turns: tuple[ReadinessTurnResult, ...]
    captured_async_jobs: int = 0

    @property
    def passed(self) -> bool:
        return all(turn.passed for turn in self.turns)

    @property
    def failed_turns(self) -> tuple[ReadinessTurnResult, ...]:
        return tuple(turn for turn in self.turns if not turn.passed)

    @property
    def latency_summary(self) -> dict[str, float | None]:
        latencies = sorted(turn.latency_ms for turn in self.turns)
        if not latencies:
            return {
                "min_ms": None,
                "max_ms": None,
                "avg_ms": None,
                "p50_ms": None,
                "p95_ms": None,
                "p99_ms": None,
            }
        return {
            "min_ms": round(latencies[0], 2),
            "max_ms": round(latencies[-1], 2),
            "avg_ms": round(sum(latencies) / len(latencies), 2),
            "p50_ms": round(_percentile(latencies, 0.50), 2),
            "p95_ms": round(_percentile(latencies, 0.95), 2),
            "p99_ms": round(_percentile(latencies, 0.99), 2),
        }

    @property
    def planner_quality_summary(self) -> dict[str, float | int | None]:
        planner_turns = [turn for turn in self.turns if turn.planner_clean is not None]
        total = len(planner_turns)
        clean_count = sum(1 for turn in planner_turns if turn.planner_clean)
        dirty_count = total - clean_count
        return {
            "turn_count": total,
            "clean_count": clean_count,
            "dirty_count": dirty_count,
            "clean_rate": round(clean_count / total, 4) if total else None,
        }

    @property
    def llm_call_summary(self) -> dict[str, Any]:
        calls = [
            call
            for turn in self.turns
            for call in turn.llm_calls
            if isinstance(call.get("duration_ms"), int | float)
        ]
        by_event: dict[str, dict[str, float | int]] = {}
        for call in calls:
            event_name = str(call.get("event_name") or "unknown")
            duration_ms = float(call["duration_ms"])
            event_summary = by_event.setdefault(
                event_name,
                {
                    "call_count": 0,
                    "total_duration_ms": 0.0,
                    "max_duration_ms": 0.0,
                    "prompt_token_estimate": 0,
                    "output_token_estimate": 0,
                    "output_compact_token_estimate": 0,
                    "output_expanded_token_estimate": 0,
                    "output_expanded_json_chars": 0,
                    "output_default_overhead_chars": 0,
                    "output_null_field_count": 0,
                    "output_empty_field_count": 0,
                    "response_schema_json_chars": 0,
                    "response_schema_token_estimate": 0,
                    "response_schema_defs_count": 0,
                    "response_schema_action_variant_count": 0,
                    "provider_input_tokens": 0,
                    "provider_output_tokens": 0,
                    "provider_total_tokens": 0,
                    "provider_cached_tokens": 0,
                    "provider_reasoning_tokens": 0,
                    "client_http_request_count": 0,
                    "client_http_response_headers_ms": 0.0,
                    "client_http_total_ms": 0.0,
                },
            )
            event_summary["call_count"] = int(event_summary["call_count"]) + 1
            event_summary["total_duration_ms"] = round(float(event_summary["total_duration_ms"]) + duration_ms, 2)
            event_summary["max_duration_ms"] = round(max(float(event_summary["max_duration_ms"]), duration_ms), 2)
            event_summary["prompt_token_estimate"] = int(event_summary["prompt_token_estimate"]) + int(
                call.get("prompt_token_estimate") or 0
            )
            event_summary["output_token_estimate"] = int(event_summary["output_token_estimate"]) + int(
                call.get("output_token_estimate") or 0
            )
            event_summary["output_compact_token_estimate"] = int(
                event_summary["output_compact_token_estimate"]
            ) + int(call.get("output_compact_token_estimate") or 0)
            event_summary["output_expanded_token_estimate"] = int(
                event_summary["output_expanded_token_estimate"]
            ) + int(call.get("output_expanded_token_estimate") or 0)
            event_summary["output_expanded_json_chars"] = int(event_summary["output_expanded_json_chars"]) + int(
                call.get("output_expanded_json_chars") or 0
            )
            event_summary["output_default_overhead_chars"] = int(
                event_summary["output_default_overhead_chars"]
            ) + int(call.get("output_default_overhead_chars") or 0)
            event_summary["output_null_field_count"] = int(event_summary["output_null_field_count"]) + int(
                call.get("output_null_field_count") or 0
            )
            event_summary["output_empty_field_count"] = int(event_summary["output_empty_field_count"]) + int(
                call.get("output_empty_field_count") or 0
            )
            event_summary["response_schema_json_chars"] = int(event_summary["response_schema_json_chars"]) + int(
                call.get("response_schema_json_chars") or 0
            )
            event_summary["response_schema_token_estimate"] = int(
                event_summary["response_schema_token_estimate"]
            ) + int(call.get("response_schema_token_estimate") or 0)
            event_summary["response_schema_defs_count"] = max(
                int(event_summary["response_schema_defs_count"]),
                int(call.get("response_schema_defs_count") or 0),
            )
            event_summary["response_schema_action_variant_count"] = max(
                int(event_summary["response_schema_action_variant_count"]),
                int(call.get("response_schema_action_variant_count") or 0),
            )
            event_summary["provider_input_tokens"] = int(event_summary["provider_input_tokens"]) + int(
                call.get("provider_input_tokens") or 0
            )
            event_summary["provider_output_tokens"] = int(event_summary["provider_output_tokens"]) + int(
                call.get("provider_output_tokens") or 0
            )
            event_summary["provider_total_tokens"] = int(event_summary["provider_total_tokens"]) + int(
                call.get("provider_total_tokens") or 0
            )
            event_summary["provider_cached_tokens"] = int(event_summary["provider_cached_tokens"]) + int(
                call.get("provider_cached_tokens") or 0
            )
            event_summary["provider_reasoning_tokens"] = int(event_summary["provider_reasoning_tokens"]) + int(
                call.get("provider_reasoning_tokens") or 0
            )
            if int(event_summary["provider_input_tokens"]) > 0:
                event_summary["provider_cache_hit_rate"] = round(
                    int(event_summary["provider_cached_tokens"]) / int(event_summary["provider_input_tokens"]),
                    4,
                )
            event_summary["client_http_request_count"] = int(event_summary["client_http_request_count"]) + int(
                call.get("client_http_request_count") or 0
            )
            event_summary["client_http_response_headers_ms"] = round(
                max(
                    float(event_summary["client_http_response_headers_ms"]),
                    float(call.get("client_http_response_headers_ms") or 0.0),
                ),
                2,
            )
            event_summary["client_http_total_ms"] = round(
                max(
                    float(event_summary["client_http_total_ms"]),
                    float(call.get("client_http_total_ms") or 0.0),
                ),
                2,
            )
        total_duration_ms = sum(float(call["duration_ms"]) for call in calls)
        return {
            "call_count": len(calls),
            "total_duration_ms": round(total_duration_ms, 2),
            "max_duration_ms": round(max((float(call["duration_ms"]) for call in calls), default=0.0), 2),
            "by_event": by_event,
        }

    @property
    def slowest_llm_calls(self) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for turn in self.turns:
            for call in turn.llm_calls:
                flattened.append(
                    {
                        "scenario_id": turn.scenario_id,
                        "turn_index": turn.turn_index,
                        "user_text": turn.user_text,
                        **call,
                    }
                )
        flattened.sort(key=lambda call: float(call.get("duration_ms") or 0.0), reverse=True)
        return flattened[:10]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scenario_ids": list(self.scenario_ids),
            "passed": self.passed,
            "turn_count": len(self.turns),
            "passed_count": sum(1 for turn in self.turns if turn.passed),
            "failed_count": len(self.failed_turns),
            "captured_async_jobs": self.captured_async_jobs,
            "latency_summary": self.latency_summary,
            "planner_quality_summary": self.planner_quality_summary,
            "planner_clean_rate": self.planner_quality_summary["clean_rate"],
            "llm_call_summary": self.llm_call_summary,
            "slowest_llm_calls": self.slowest_llm_calls,
            "slowest_turns": [
                {
                    "scenario_id": turn.scenario_id,
                    "turn_index": turn.turn_index,
                    "user_text": turn.user_text,
                    "latency_ms": round(turn.latency_ms, 2),
                    "llm_total_ms": round(_llm_call_total_ms(turn.llm_calls), 2),
                    "semantic_path_shape": turn.route_metadata.get("semantic_path_shape"),
                    "routing_owner": turn.route_metadata.get("routing_owner"),
                    "routing_decision": turn.route_metadata.get("routing_decision"),
                    "planner_clean": turn.planner_clean,
                    "task_types": list(turn.task_types),
                }
                for turn in sorted(self.turns, key=lambda item: item.latency_ms, reverse=True)[:5]
            ],
            "turns": [turn.to_dict() for turn in self.turns],
        }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    bounded = max(0.0, min(1.0, fraction))
    index = bounded * (len(sorted_values) - 1)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def _llm_call_total_ms(llm_calls: tuple[dict[str, Any], ...]) -> float:
    return sum(float(call.get("duration_ms") or 0.0) for call in llm_calls)
