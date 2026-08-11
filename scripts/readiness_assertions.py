"""Assertion helpers for readiness scenario turns."""

from __future__ import annotations

from collections import Counter
from typing import Any

from scripts.readiness_models import ReadinessMode, ReadinessTurn
from scripts.readiness_rendering import duplicate_visible_blocks


def _directive_field(metadata: dict[str, Any], field: str) -> Any:
    directive = metadata.get("turn_directive")
    if isinstance(directive, dict):
        return directive.get(field)
    return getattr(directive, field, None)


def _snapshot_value(snapshot: dict[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = snapshot
    for component in path.split("."):
        if not isinstance(value, dict) or component not in value:
            return False, None
        value = value[component]
    return True, value


def _money_topics(async_jobs: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(
        str(job.get("topic") or "")
        for job in async_jobs
        if any(marker in str(job.get("topic") or "").casefold() for marker in ("transfer", "airtime", "data"))
    )


def assert_readiness_turn(
    turn: ReadinessTurn,
    response: str,
    *,
    route_metadata: dict[str, Any] | None = None,
    task_types: tuple[str, ...] = (),
    async_jobs: tuple[dict[str, Any], ...] = (),
    llm_calls: tuple[dict[str, Any], ...] = (),
    state_snapshot: dict[str, Any] | None = None,
    previous_state_snapshot: dict[str, Any] | None = None,
    enforce_route_expectations: bool = True,
    mode: ReadinessMode | None = None,
) -> tuple[bool, tuple[str, ...]]:
    expectation = turn.expectation
    lowered = response.lower()
    errors: list[str] = []
    if (
        expectation.expect_response_required
        and (mode is None or mode in expectation.response_required_modes)
        and not response.strip()
        and not task_types
    ):
        errors.append("expected a visible recovery response")
    if mode is None or mode in expectation.response_content_modes:
        if expectation.expect_response_any and not any(
            expected.casefold() in lowered for expected in expectation.expect_response_any
        ):
            errors.append(f"expected response to include one of: {', '.join(expectation.expect_response_any)}")
        for forbidden in expectation.expect_response_none:
            if forbidden.casefold() in lowered:
                errors.append(f"response must not include: {forbidden}")
    if expectation.expect_any and not any(expected.lower() in lowered for expected in expectation.expect_any):
        errors.append(f"expected any of: {', '.join(expectation.expect_any)}")
    for expected in expectation.expect_all:
        if expected.lower() not in lowered:
            errors.append(f"expected: {expected}")
    for forbidden in expectation.expect_none:
        if forbidden.lower() in lowered:
            errors.append(f"did not expect: {forbidden}")

    duplicates = duplicate_visible_blocks(response)
    if duplicates and not expectation.allow_duplicate_blocks:
        errors.append(f"duplicate visible response block: {duplicates[0]!r}")

    route_metadata = route_metadata or {}
    if enforce_route_expectations:
        route_expectations = (
            (
                "turn_directive.path_shape",
                expectation.expect_path_shape,
                _directive_field(route_metadata, "path_shape"),
            ),
            ("turn_directive.owner", expectation.expect_routing_owner, _directive_field(route_metadata, "owner")),
            (
                "turn_directive.decision",
                expectation.expect_routing_decision,
                _directive_field(route_metadata, "decision"),
            ),
        )
        for key, expected, actual in route_expectations:
            if expected is not None and actual != expected:
                errors.append(f"expected {key}={expected!r}; got {actual!r}")

    if expectation.expect_task_types is not None and task_types != expectation.expect_task_types:
        errors.append(f"expected task types {expectation.expect_task_types}; got {task_types}")

    if expectation.expect_allowed_task_types is not None:
        unexpected = tuple(
            task_type for task_type in task_types if task_type not in expectation.expect_allowed_task_types
        )
        if unexpected:
            errors.append(f"task types outside allowed set {expectation.expect_allowed_task_types}: {unexpected}")

    forbidden_tasks = tuple(
        task_type for task_type in task_types if task_type in expectation.expect_forbidden_task_types
    )
    if forbidden_tasks:
        errors.append(f"forbidden task types created: {forbidden_tasks}")

    if (
        expectation.expect_async_job_count_delta is not None
        and len(async_jobs) != expectation.expect_async_job_count_delta
    ):
        errors.append(f"expected {expectation.expect_async_job_count_delta} async jobs; got {len(async_jobs)}")

    if expectation.expect_async_job_count_max is not None and len(async_jobs) > expectation.expect_async_job_count_max:
        errors.append(f"expected at most {expectation.expect_async_job_count_max} async jobs; got {len(async_jobs)}")

    if expectation.expect_no_money_movement:
        money_topics = _money_topics(async_jobs)
        if money_topics:
            errors.append(f"unsafe money movement jobs captured: {money_topics}")

    effect_expectation = expectation.effect_expectation
    if effect_expectation is not None:
        money_topics = _money_topics(async_jobs)
        if (
            effect_expectation.exact_money_movement_jobs is not None
            and len(money_topics) != effect_expectation.exact_money_movement_jobs
        ):
            errors.append(
                "expected "
                f"{effect_expectation.exact_money_movement_jobs} money-movement jobs; got {len(money_topics)}"
            )
        if (
            effect_expectation.max_money_movement_jobs is not None
            and len(money_topics) > effect_expectation.max_money_movement_jobs
        ):
            errors.append(
                "expected at most "
                f"{effect_expectation.max_money_movement_jobs} money-movement jobs; got {len(money_topics)}"
            )
        for topic in effect_expectation.required_topics:
            if not any(topic.casefold() in actual.casefold() for actual in money_topics):
                errors.append(f"expected money-movement topic containing {topic!r}")
        for topic in effect_expectation.forbidden_topics:
            if any(topic.casefold() in actual.casefold() for actual in money_topics):
                errors.append(f"forbidden money-movement topic: {topic!r}")

    if expectation.state_invariants:
        snapshot = state_snapshot or {}
        previous = previous_state_snapshot or {}
        for invariant in expectation.state_invariants:
            exists, actual = _snapshot_value(snapshot, invariant.path)
            if invariant.mode == "present" and not exists:
                errors.append(f"expected state path to exist: {invariant.path}")
            elif invariant.mode == "absent" and exists:
                errors.append(f"expected state path to be absent: {invariant.path}")
            elif invariant.mode == "equals" and (not exists or actual != invariant.value):
                errors.append(
                    f"expected state {invariant.path}={invariant.value!r}; got {actual!r}"
                )
            elif invariant.mode in {"contains", "not_contains"}:
                if isinstance(actual, (list, tuple, set, frozenset)) or (
                    isinstance(actual, str) and isinstance(invariant.value, str)
                ):
                    contained = invariant.value in actual
                else:
                    contained = False
                if invariant.mode == "contains" and (not exists or not contained):
                    errors.append(f"expected state {invariant.path} to contain {invariant.value!r}; got {actual!r}")
                elif invariant.mode == "not_contains" and exists and contained:
                    errors.append(
                        f"expected state {invariant.path} not to contain {invariant.value!r}; got {actual!r}"
                    )
            elif invariant.mode == "preserve":
                previous_exists, previous_value = _snapshot_value(previous, invariant.path)
                if not exists or not previous_exists or actual != previous_value:
                    errors.append(f"expected state path to be preserved: {invariant.path}")

    if expectation.expect_async_job_topics is not None:
        topics = tuple(str(job.get("topic") or "") for job in async_jobs)
        if topics != expectation.expect_async_job_topics:
            errors.append(f"expected async job topics {expectation.expect_async_job_topics}; got {topics}")

    if expectation.expect_planner_clean is not None:
        planner_clean = route_metadata.get("planner_clean")
        if planner_clean != expectation.expect_planner_clean:
            dirty_reasons = route_metadata.get("planner_dirty_reasons") or ()
            dirty_reasons_display = list(dirty_reasons) if isinstance(dirty_reasons, (list, tuple)) else dirty_reasons
            errors.append(
                " ".join(
                    (
                        f"expected planner_clean={expectation.expect_planner_clean!r};",
                        f"got {planner_clean!r};",
                        f"dirty_reasons={dirty_reasons_display!r}",
                    )
                )
            )

    if expectation.expect_llm_call_count is not None and len(llm_calls) != expectation.expect_llm_call_count:
        errors.append(f"expected {expectation.expect_llm_call_count} LLM calls; got {len(llm_calls)}")

    if expectation.expect_llm_event_counts:
        event_counts = Counter(str(call.get("event_name") or "unknown") for call in llm_calls)
        for event_name, expected_count in expectation.expect_llm_event_counts:
            actual_count = event_counts[event_name]
            if actual_count != expected_count:
                errors.append(f"expected {expected_count} {event_name} calls; got {actual_count}")

    if expectation.llm_call_budget is not None:
        budget_status, budget_violations = expectation.llm_call_budget.evaluate(llm_calls, mode=mode)
        if budget_status == "exceeded":
            errors.extend(f"LLM budget exceeded: {violation}" for violation in budget_violations)

    metadata_expectations = {
        "active_domain": expectation.expect_active_domain,
        "session_state": expectation.expect_session_state,
        "clarification_type": expectation.expect_clarification_type,
        "query_session_source": expectation.expect_context_source,
    }
    for key, expected in metadata_expectations.items():
        if expected is not None and route_metadata.get(key) != expected:
            errors.append(f"expected {key}={expected!r}; got {route_metadata.get(key)!r}")
    for key, expected in expectation.expect_state_fields:
        if route_metadata.get(key) != expected:
            errors.append(f"expected state field {key}={expected!r}; got {route_metadata.get(key)!r}")

    return not errors, tuple(errors)


def task_types_from_response(response: dict[str, Any]) -> tuple[str, ...]:
    """Infer readiness task labels from public orchestrator response metadata."""

    task_types_raw = response.get("task_types")
    if isinstance(task_types_raw, list):
        return tuple(str(item) for item in task_types_raw)

    task_executors_raw = response.get("task_executors")
    if isinstance(task_executors_raw, list):
        return tuple(str(item) for item in task_executors_raw)

    expected_raw = response.get("expected_transaction_executors")
    if isinstance(expected_raw, list):
        return tuple(str(item) for item in expected_raw)

    route_domain = _directive_field(response, "target_domain")
    if isinstance(route_domain, str) and route_domain:
        return (route_domain,)

    path_shape = _directive_field(response, "path_shape")
    if not isinstance(path_shape, str):
        return ()
    if "transfer" in path_shape:
        return ("transfer",)
    if "airtime" in path_shape:
        return ("airtime",)
    if "data" in path_shape:
        return ("data",)
    if "schedule" in path_shape:
        return ("schedule",)
    if "beneficiary" in path_shape:
        return ("beneficiary",)
    if "account" in path_shape or "balance" in path_shape:
        return ("account",)
    if "query" in path_shape:
        return ("query",)
    if "support" in path_shape:
        return ("support",)
    return ()
