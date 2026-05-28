"""Assertion helpers for readiness scenario turns."""

from __future__ import annotations

from typing import Any

from scripts.readiness_models import ReadinessTurn
from scripts.readiness_rendering import duplicate_visible_blocks


def assert_readiness_turn(
    turn: ReadinessTurn,
    response: str,
    *,
    route_metadata: dict[str, Any] | None = None,
    task_types: tuple[str, ...] = (),
    async_jobs: tuple[dict[str, Any], ...] = (),
    enforce_route_expectations: bool = True,
) -> tuple[bool, tuple[str, ...]]:
    expectation = turn.expectation
    lowered = response.lower()
    errors: list[str] = []
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
        route_expectations = {
            "semantic_path_shape": expectation.expect_path_shape,
            "routing_owner": expectation.expect_routing_owner,
            "routing_decision": expectation.expect_routing_decision,
        }
        for key, expected in route_expectations.items():
            if expected is not None and route_metadata.get(key) != expected:
                errors.append(f"expected {key}={expected!r}; got {route_metadata.get(key)!r}")

    if expectation.expect_task_types is not None and task_types != expectation.expect_task_types:
        errors.append(f"expected task types {expectation.expect_task_types}; got {task_types}")

    if (
        expectation.expect_async_job_count_delta is not None
        and len(async_jobs) != expectation.expect_async_job_count_delta
    ):
        errors.append(f"expected {expectation.expect_async_job_count_delta} async jobs; got {len(async_jobs)}")

    if expectation.expect_async_job_topics is not None:
        topics = tuple(str(job.get("topic") or "") for job in async_jobs)
        if topics != expectation.expect_async_job_topics:
            errors.append(f"expected async job topics {expectation.expect_async_job_topics}; got {topics}")

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

    route_domain = response.get("routing_target_domain")
    if isinstance(route_domain, str) and route_domain:
        return (route_domain,)

    path_shape = response.get("semantic_path_shape")
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
