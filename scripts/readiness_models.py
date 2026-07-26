"""Data models for readiness transcript runs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

ReadinessMode = Literal["deterministic", "dry-run"]
ReadinessCriticality = Literal["safety", "correctness", "quality"]
ReadinessOutcome = Literal[
    "correct",
    "clarified",
    "recovered",
    "misrouted",
    "wrong_referent",
    "context_lost",
    "unnecessary_clarification",
    "clarification_loop",
    "unsafe_execution",
    "unsupported_gracefully",
]
LLMBudgetStatus = Literal["within_budget", "exceeded", "observed"]
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
    "query-longtail",
    "variance-insight",
    "latency",
    "llm-latency",
    "planner",
    "adversarial_bad_player",
    "system_intelligence",
    "extended_casual",
    "complex_interruptions",
    "robustness",
]


def _turn_directive_metadata(route_metadata: dict[str, Any]) -> dict[str, Any] | None:
    value = route_metadata.get("turn_directive")
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class LLMTokenBudget:
    """Per-call token ceilings for one measured LLM role/profile."""

    event_name: str
    response_type_prefix: str | None = None
    max_prompt_tokens: int | None = None
    max_schema_tokens: int | None = None
    max_provider_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def matches(self, call: dict[str, Any]) -> bool:
        if call.get("event_name") != self.event_name:
            return False
        if self.response_type_prefix is None:
            return True
        return str(call.get("response_type") or "").startswith(self.response_type_prefix)

    def violations(self, call: dict[str, Any]) -> tuple[str, ...]:
        checks = (
            ("prompt tokens", self.max_prompt_tokens, call.get("prompt_token_estimate")),
            ("schema tokens", self.max_schema_tokens, call.get("response_schema_token_estimate")),
            ("provider input tokens", self.max_provider_input_tokens, call.get("provider_input_tokens")),
            (
                "output tokens",
                self.max_output_tokens,
                call.get("provider_output_tokens", call.get("output_token_estimate")),
            ),
        )
        violations: list[str] = []
        for label, maximum, actual in checks:
            if maximum is not None and isinstance(actual, int | float) and actual > maximum:
                violations.append(f"{self.event_name} {label} <= {maximum}; got {int(actual)}")
        return tuple(violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_name": self.event_name,
            "response_type_prefix": self.response_type_prefix,
            "max_prompt_tokens": self.max_prompt_tokens,
            "max_schema_tokens": self.max_schema_tokens,
            "max_provider_input_tokens": self.max_provider_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


DEFAULT_LLM_TOKEN_BUDGETS: tuple[LLMTokenBudget, ...] = (
    LLMTokenBudget(
        "conversation_responder_llm_call",
        max_prompt_tokens=700,
        max_provider_input_tokens=900,
        max_output_tokens=80,
    ),
    LLMTokenBudget(
        "semantic_router_llm_call",
        max_prompt_tokens=1600,
        max_schema_tokens=750,
        max_provider_input_tokens=2500,
    ),
    LLMTokenBudget(
        "planner_llm_call",
        response_type_prefix="PlannerKnown",
        max_prompt_tokens=1000,
        max_schema_tokens=1000,
        max_provider_input_tokens=2200,
        max_output_tokens=180,
    ),
    LLMTokenBudget(
        "query_reasoner_llm_call",
        max_prompt_tokens=1800,
        max_provider_input_tokens=3000,
        max_output_tokens=180,
    ),
    LLMTokenBudget(
        "transfer_amendment_llm_call",
        max_prompt_tokens=1200,
        max_schema_tokens=700,
        max_provider_input_tokens=2200,
        max_output_tokens=100,
    ),
)


@dataclass(frozen=True)
class LLMCallBudget:
    """Per-turn LLM-call ceiling used by readiness checks."""

    max_calls: int | None = None
    max_event_counts: tuple[tuple[str, int], ...] = ()
    required_event_counts: tuple[tuple[str, int], ...] = ()
    token_budgets: tuple[LLMTokenBudget, ...] = DEFAULT_LLM_TOKEN_BUDGETS
    observe: bool = False
    enforced_modes: tuple[ReadinessMode, ...] = ("deterministic", "dry-run")

    def __post_init__(self) -> None:
        limits = (
            self.max_calls,
            *(limit for _, limit in self.max_event_counts),
            *(limit for _, limit in self.required_event_counts),
        )
        if any(limit is not None and limit < 0 for limit in limits):
            raise ValueError("LLM call budgets cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "max_event_counts": dict(self.max_event_counts),
            "required_event_counts": dict(self.required_event_counts),
            "token_budgets": [budget.to_dict() for budget in self.token_budgets],
            "observe": self.observe,
            "enforced_modes": list(self.enforced_modes),
        }

    def evaluate(
        self,
        llm_calls: tuple[dict[str, Any], ...],
        *,
        mode: ReadinessMode | None = None,
    ) -> tuple[LLMBudgetStatus, tuple[str, ...]]:
        event_counts = Counter(str(call.get("event_name") or "unknown") for call in llm_calls)
        violations: list[str] = []
        if self.max_calls is not None and len(llm_calls) > self.max_calls:
            violations.append(f"at most {self.max_calls} total calls; got {len(llm_calls)}")
        for event_name, maximum in self.max_event_counts:
            actual = event_counts[event_name]
            if actual > maximum:
                violations.append(f"at most {maximum} {event_name} calls; got {actual}")
        for event_name, minimum in self.required_event_counts:
            actual = event_counts[event_name]
            if actual < minimum:
                violations.append(f"at least {minimum} {event_name} calls; got {actual}")
        for call in llm_calls:
            for token_budget in self.token_budgets:
                if token_budget.matches(call):
                    violations.extend(token_budget.violations(call))
        if self.observe or (mode is not None and mode not in self.enforced_modes):
            return "observed", tuple(violations)
        return ("exceeded" if violations else "within_budget"), tuple(violations)


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
    llm_call_budget: LLMCallBudget | None = None
    allow_duplicate_blocks: bool = False
    expect_allowed_task_types: tuple[str, ...] | None = None
    expect_forbidden_task_types: tuple[str, ...] = ()
    expect_active_domain: str | None = None
    expect_session_state: str | None = None
    expect_clarification_type: str | None = None
    expect_context_source: str | None = None
    expect_async_job_count_max: int | None = None
    expect_no_money_movement: bool = False
    expect_state_fields: tuple[tuple[str, Any], ...] = ()
    expect_response_required: bool = False
    response_required_modes: tuple[ReadinessMode, ...] = ("dry-run",)
    expect_response_any: tuple[str, ...] = ()
    expect_response_none: tuple[str, ...] = ()
    response_content_modes: tuple[ReadinessMode, ...] = ("dry-run",)
    expected_outcome: ReadinessOutcome = "correct"


@dataclass(frozen=True)
class ReadinessTurn:
    text: str
    expectation: ReadinessExpectation = field(default_factory=ReadinessExpectation)
    pin_after: bool = False
    pin_flow_type: str = "transaction"
    modes: tuple[ReadinessMode, ...] = ("deterministic", "dry-run")
    mutation_id: str | None = None
    # Some acceptance scenarios deliberately contain independent conversations
    # under one report. Reset only durable conversational context before this
    # turn; fixture data and the selected demo user remain unchanged.
    reset_context_before: bool = False


@dataclass(frozen=True)
class ReadinessScenario:
    id: str
    turns: tuple[ReadinessTurn, ...]
    description: str = ""
    category: str = "uncategorized"
    tags: tuple[str, ...] = ()
    criticality: ReadinessCriticality = "correctness"


@dataclass(frozen=True)
class ReadinessInvocation:
    response: dict[str, Any]
    route_metadata: dict[str, Any] = field(default_factory=dict)
    task_types: tuple[str, ...] = ()
    async_jobs: tuple[dict[str, Any], ...] = ()
    llm_calls: tuple[dict[str, Any], ...] = ()
    turn_timing: dict[str, Any] = field(default_factory=dict)


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
    turn_timing: dict[str, Any] = field(default_factory=dict)
    llm_budget: LLMCallBudget | None = None
    llm_budget_status: LLMBudgetStatus = "observed"
    llm_budget_violations: tuple[str, ...] = ()
    planner_clean: bool | None = None
    planner_dirty_reasons: tuple[str, ...] = ()
    category: str = "uncategorized"
    criticality: ReadinessCriticality = "correctness"
    outcome: ReadinessOutcome = "correct"
    mutation_id: str | None = None

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
            "turn_timing": self.turn_timing,
            "llm_total_ms": round(_llm_call_total_ms(self.llm_calls), 2),
            "llm_event_chain": list(_llm_event_chain(self.llm_calls)),
            "route_signature": _route_signature(self.route_metadata),
            "llm_budget": self.llm_budget.to_dict() if self.llm_budget else None,
            "llm_budget_status": self.llm_budget_status,
            "llm_budget_violations": list(self.llm_budget_violations),
            "planner_clean": self.planner_clean,
            "planner_dirty_reasons": list(self.planner_dirty_reasons),
            "category": self.category,
            "criticality": self.criticality,
            "outcome": self.outcome,
            "mutation_id": self.mutation_id,
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
    def robustness_summary(self) -> dict[str, Any]:
        total = len(self.turns)
        by_outcome: dict[str, int] = {}
        by_category: dict[str, dict[str, int]] = {}
        for turn in self.turns:
            by_outcome[turn.outcome] = by_outcome.get(turn.outcome, 0) + 1
            category = by_category.setdefault(turn.category, {"total": 0, "passed": 0})
            category["total"] += 1
            category["passed"] += int(turn.passed)
        return {
            "turn_count": total,
            "passed": sum(1 for turn in self.turns if turn.passed),
            "pass_rate": round(sum(1 for turn in self.turns if turn.passed) / total, 4) if total else None,
            "by_outcome": by_outcome,
            "by_category": by_category,
            "unsafe_execution_count": by_outcome.get("unsafe_execution", 0),
        }

    @property
    def llm_call_summary(self) -> dict[str, Any]:
        calls = [
            call for turn in self.turns for call in turn.llm_calls if isinstance(call.get("duration_ms"), int | float)
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
                    "provider_uncached_input_tokens": 0,
                    "provider_reasoning_tokens": 0,
                    "client_http_request_count": 0,
                    "client_http_retry_count": 0,
                    "client_http_response_headers_ms": 0.0,
                    "client_http_body_processing_ms": 0.0,
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
            event_summary["output_compact_token_estimate"] = int(event_summary["output_compact_token_estimate"]) + int(
                call.get("output_compact_token_estimate") or 0
            )
            event_summary["output_expanded_token_estimate"] = int(
                event_summary["output_expanded_token_estimate"]
            ) + int(call.get("output_expanded_token_estimate") or 0)
            event_summary["output_expanded_json_chars"] = int(event_summary["output_expanded_json_chars"]) + int(
                call.get("output_expanded_json_chars") or 0
            )
            event_summary["output_default_overhead_chars"] = int(event_summary["output_default_overhead_chars"]) + int(
                call.get("output_default_overhead_chars") or 0
            )
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
            event_summary["provider_uncached_input_tokens"] = int(
                event_summary["provider_uncached_input_tokens"]
            ) + int(call.get("provider_uncached_input_tokens") or 0)
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
            event_summary["client_http_retry_count"] = int(event_summary["client_http_retry_count"]) + int(
                call.get("client_http_retry_count") or 0
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
            event_summary["client_http_body_processing_ms"] = round(
                max(
                    float(event_summary["client_http_body_processing_ms"]),
                    float(call.get("client_http_body_processing_ms") or 0.0),
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
    def llm_health_summary(self) -> dict[str, Any]:
        """Report model-call reliability without changing conversational pass/fail.

        A response-schema validation error can still leave a safe fallback response.
        Keeping it separate prevents a safe transcript from concealing an unhealthy
        model integration, while avoiding an arbitrary reclassification of the turn.
        """

        calls = [call for turn in self.turns for call in turn.llm_calls]
        errors = [call for call in calls if isinstance(call.get("error_type"), str) and call["error_type"]]
        error_types: dict[str, int] = {}
        http_statuses: dict[str, int] = {}
        for call in calls:
            error_type = call.get("error_type")
            if isinstance(error_type, str) and error_type:
                error_types[error_type] = error_types.get(error_type, 0) + 1
            status = call.get("client_http_status_code")
            if isinstance(status, int):
                status_key = str(status)
                http_statuses[status_key] = http_statuses.get(status_key, 0) + 1
        provider_error_calls = sum(
            1
            for call in errors
            if isinstance(call.get("client_http_status_code"), int) and call["client_http_status_code"] >= 400
        )
        validation_error_calls = error_types.get("ValidationError", 0)
        return {
            "call_count": len(calls),
            "error_call_count": len(errors),
            "error_rate": round(len(errors) / len(calls), 4) if calls else None,
            "provider_error_call_count": provider_error_calls,
            "validation_error_call_count": validation_error_calls,
            "error_types": error_types,
            "http_statuses": http_statuses,
            "degraded": bool(provider_error_calls or validation_error_calls),
        }

    @property
    def llm_audit_summary(self) -> list[dict[str, Any]]:
        """Group call costs by route and ordered chain without user content."""
        groups: dict[tuple[str, tuple[str, ...]], list[ReadinessTurnResult]] = {}
        for turn in self.turns:
            key = (_route_signature(turn.route_metadata), _llm_event_chain(turn.llm_calls))
            groups.setdefault(key, []).append(turn)

        summary: list[dict[str, Any]] = []
        for (route_signature, event_chain), turns in groups.items():
            totals = sorted(_llm_call_total_ms(turn.llm_calls) for turn in turns)
            calls = [call for turn in turns for call in turn.llm_calls]
            provider_input_tokens = sum(int(call.get("provider_input_tokens") or 0) for call in calls)
            provider_cached_tokens = sum(int(call.get("provider_cached_tokens") or 0) for call in calls)
            summary.append(
                {
                    "route_signature": route_signature,
                    "event_chain": list(event_chain),
                    "turn_count": len(turns),
                    "call_count": len(calls),
                    "llm_total_ms_p50": round(_percentile(totals, 0.50), 2) if totals else 0.0,
                    "llm_total_ms_p95": round(_percentile(totals, 0.95), 2) if totals else 0.0,
                    "llm_total_ms_max": round(max(totals, default=0.0), 2),
                    "prompt_token_estimate": sum(int(call.get("prompt_token_estimate") or 0) for call in calls),
                    "output_token_estimate": sum(int(call.get("output_token_estimate") or 0) for call in calls),
                    "response_schema_token_estimate": sum(
                        int(call.get("response_schema_token_estimate") or 0) for call in calls
                    ),
                    "provider_input_tokens": provider_input_tokens,
                    "provider_cached_tokens": provider_cached_tokens,
                    "provider_uncached_input_tokens": sum(
                        int(call.get("provider_uncached_input_tokens") or 0) for call in calls
                    ),
                    "provider_cache_hit_rate": round(provider_cached_tokens / provider_input_tokens, 4)
                    if provider_input_tokens
                    else None,
                    "budget_statuses": dict(Counter(turn.llm_budget_status for turn in turns)),
                }
            )
        return sorted(
            summary,
            key=lambda item: (float(item["llm_total_ms_p95"]), int(item["call_count"])),
            reverse=True,
        )

    @property
    def route_latency_summary(self) -> list[dict[str, Any]]:
        """Aggregate user-visible and completion timing by canonical route."""
        groups: dict[str, list[ReadinessTurnResult]] = {}
        for turn in self.turns:
            groups.setdefault(_route_signature(turn.route_metadata), []).append(turn)

        summary: list[dict[str, Any]] = []
        for route_signature, turns in groups.items():
            completion = sorted(float(turn.turn_timing.get("end_to_end_ms") or turn.latency_ms) for turn in turns)
            final_ready = sorted(
                float(turn.turn_timing.get("end_to_end_final_ready_ms") or turn.latency_ms) for turn in turns
            )
            first_visible = sorted(
                float(turn.turn_timing.get("end_to_end_first_visible_ms") or turn.latency_ms) for turn in turns
            )
            outer_overhead = sorted(float(turn.turn_timing.get("outside_graph_ms") or 0.0) for turn in turns)
            heartbeat_sent = sum(bool(turn.turn_timing.get("heartbeat_sent")) for turn in turns)
            summary.append(
                {
                    "route_signature": route_signature,
                    "turn_count": len(turns),
                    "completion_ms_p50": round(_percentile(completion, 0.50), 2),
                    "completion_ms_p95": round(_percentile(completion, 0.95), 2),
                    "final_ready_ms_p50": round(_percentile(final_ready, 0.50), 2),
                    "final_ready_ms_p95": round(_percentile(final_ready, 0.95), 2),
                    "first_visible_ms_p50": round(_percentile(first_visible, 0.50), 2),
                    "first_visible_ms_p95": round(_percentile(first_visible, 0.95), 2),
                    "outside_graph_ms_p95": round(_percentile(outer_overhead, 0.95), 2),
                    "heartbeat_turn_count": heartbeat_sent,
                }
            )
        return sorted(summary, key=lambda item: float(item["completion_ms_p95"]), reverse=True)

    @property
    def llm_audit_candidates(self) -> list[dict[str, Any]]:
        """Return budget breaches and multi-call observed turns without user text."""
        candidates = [
            {
                "scenario_id": turn.scenario_id,
                "turn_index": turn.turn_index,
                "route_signature": _route_signature(turn.route_metadata),
                "event_chain": list(_llm_event_chain(turn.llm_calls)),
                "llm_call_count": len(turn.llm_calls),
                "llm_total_ms": round(_llm_call_total_ms(turn.llm_calls), 2),
                "budget_status": turn.llm_budget_status,
                "budget_violations": list(turn.llm_budget_violations),
            }
            for turn in self.turns
            if turn.llm_budget_status == "exceeded"
            or (turn.llm_budget_status == "observed" and len(turn.llm_calls) > 1)
        ]
        return sorted(
            candidates,
            key=lambda item: (item["budget_status"] == "exceeded", item["llm_call_count"], item["llm_total_ms"]),
            reverse=True,
        )

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
            "robustness_summary": self.robustness_summary,
            "llm_call_summary": self.llm_call_summary,
            "llm_health_summary": self.llm_health_summary,
            "llm_audit_summary": self.llm_audit_summary,
            "route_latency_summary": self.route_latency_summary,
            "llm_audit_candidates": self.llm_audit_candidates,
            "slowest_llm_calls": self.slowest_llm_calls,
            "slowest_turns": [
                {
                    "scenario_id": turn.scenario_id,
                    "turn_index": turn.turn_index,
                    "user_text": turn.user_text,
                    "latency_ms": round(turn.latency_ms, 2),
                    "llm_total_ms": round(_llm_call_total_ms(turn.llm_calls), 2),
                    "path_shape": (_turn_directive_metadata(turn.route_metadata) or {}).get("path_shape"),
                    "turn_directive": _turn_directive_metadata(turn.route_metadata),
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


def _llm_event_chain(llm_calls: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(str(call.get("event_name") or "unknown") for call in llm_calls)


def _route_signature(route_metadata: dict[str, Any]) -> str:
    directive = _turn_directive_metadata(route_metadata) or {}
    return "/".join(str(directive.get(field) or "unknown") for field in ("path_shape", "owner", "decision"))
