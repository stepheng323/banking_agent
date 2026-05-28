"""Readiness scenario sequencing."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from scripts.readiness_assertions import assert_readiness_turn
from scripts.readiness_models import (
    ReadinessInvocation,
    ReadinessMode,
    ReadinessRunResult,
    ReadinessScenario,
    ReadinessTurn,
    ReadinessTurnResult,
)
from scripts.readiness_rendering import render_orchestrator_result


async def run_readiness_sequence(
    *,
    mode: ReadinessMode,
    scenarios: tuple[ReadinessScenario, ...],
    invoke_turn: Callable[[ReadinessScenario, ReadinessTurn, int], Awaitable[ReadinessInvocation]],
    before_scenario: Callable[[ReadinessScenario], Awaitable[None]] | None = None,
    stop_on_fail: bool = False,
    enforce_route_expectations: bool = True,
) -> ReadinessRunResult:
    results: list[ReadinessTurnResult] = []
    captured_async_jobs = 0
    for scenario in scenarios:
        if before_scenario is not None:
            await before_scenario(scenario)
        mode_turns = tuple(turn for turn in scenario.turns if mode in turn.modes)
        for index, turn in enumerate(mode_turns, start=1):
            started = time.perf_counter()
            invocation = await invoke_turn(scenario, turn, index)
            elapsed_ms = (time.perf_counter() - started) * 1000
            rendered = render_orchestrator_result(invocation.response)
            captured_async_jobs += len(invocation.async_jobs)
            passed, errors = assert_readiness_turn(
                turn,
                rendered,
                route_metadata=invocation.route_metadata,
                task_types=invocation.task_types,
                async_jobs=invocation.async_jobs,
                enforce_route_expectations=enforce_route_expectations,
            )
            result = ReadinessTurnResult(
                scenario_id=scenario.id,
                turn_index=index,
                user_text=turn.text,
                response_text=rendered,
                latency_ms=elapsed_ms,
                passed=passed,
                errors=errors,
                route_metadata=invocation.route_metadata,
                task_types=invocation.task_types,
                async_jobs=invocation.async_jobs,
            )
            results.append(result)
            if stop_on_fail and not passed:
                return ReadinessRunResult(
                    mode=mode,
                    scenario_ids=tuple(scenario.id for scenario in scenarios),
                    turns=tuple(results),
                    captured_async_jobs=captured_async_jobs,
                )
    return ReadinessRunResult(
        mode=mode,
        scenario_ids=tuple(scenario.id for scenario in scenarios),
        turns=tuple(results),
        captured_async_jobs=captured_async_jobs,
    )
