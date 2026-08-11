"""Readiness scenario sequencing."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from scripts.readiness_assertions import assert_readiness_turn
from scripts.readiness_models import (
    ReadinessInvocation,
    ReadinessMode,
    ReadinessOutcome,
    ReadinessRunResult,
    ReadinessScenario,
    ReadinessTurn,
    ReadinessTurnResult,
)
from scripts.readiness_rendering import render_orchestrator_result


def _planner_clean_from_metadata(route_metadata: dict[str, object]) -> bool | None:
    value = route_metadata.get("planner_clean")
    return value if isinstance(value, bool) else None


def _planner_dirty_reasons_from_metadata(route_metadata: dict[str, object]) -> tuple[str, ...]:
    raw_reasons = route_metadata.get("planner_dirty_reasons") or ()
    if not isinstance(raw_reasons, (list, tuple)):
        return ()
    return tuple(str(reason) for reason in raw_reasons if str(reason).strip())


async def run_readiness_sequence(
    *,
    mode: ReadinessMode,
    scenarios: tuple[ReadinessScenario, ...],
    invoke_turn: Callable[[ReadinessScenario, ReadinessTurn, int], Awaitable[ReadinessInvocation]],
    before_scenario: Callable[[ReadinessScenario], Awaitable[None]] | None = None,
    before_turn: Callable[[ReadinessScenario, ReadinessTurn, int], Awaitable[None]] | None = None,
    stop_on_fail: bool = False,
    enforce_route_expectations: bool = True,
) -> ReadinessRunResult:
    results: list[ReadinessTurnResult] = []
    captured_async_jobs = 0
    previous_snapshots: dict[str, dict[str, object]] = {}
    for scenario in scenarios:
        if before_scenario is not None:
            await before_scenario(scenario)
        mode_turns = tuple(turn for turn in scenario.turns if mode in turn.modes)
        for index, turn in enumerate(mode_turns, start=1):
            if before_turn is not None:
                await before_turn(scenario, turn, index)
            if turn.reset_context_before:
                previous_snapshots[scenario.id] = {}
            started = time.perf_counter()
            invocation = await invoke_turn(scenario, turn, index)
            elapsed_ms = (time.perf_counter() - started) * 1000
            turn_timing = dict(invocation.turn_timing)
            graph_total_ms = float(turn_timing.get("turn_total_ms") or 0.0)
            outer_overhead_ms = max(0.0, elapsed_ms - graph_total_ms)
            turn_timing.update(
                {
                    "end_to_end_ms": round(elapsed_ms, 2),
                    "outside_graph_ms": round(outer_overhead_ms, 2),
                    "end_to_end_final_ready_ms": round(
                        outer_overhead_ms + float(turn_timing.get("final_response_ready_ms") or graph_total_ms),
                        2,
                    ),
                    "end_to_end_first_visible_ms": round(
                        outer_overhead_ms + float(turn_timing.get("first_visible_output_ms") or graph_total_ms),
                        2,
                    ),
                }
            )
            rendered = render_orchestrator_result(invocation.response)
            captured_async_jobs += len(invocation.async_jobs)
            passed, errors = assert_readiness_turn(
                turn,
                rendered,
                route_metadata=invocation.route_metadata,
                task_types=invocation.task_types,
                async_jobs=invocation.async_jobs,
                llm_calls=invocation.llm_calls,
                state_snapshot=invocation.state_snapshot,
                previous_state_snapshot=previous_snapshots.get(scenario.id, {}),
                enforce_route_expectations=enforce_route_expectations,
                mode=mode,
            )
            budget = turn.expectation.llm_call_budget
            budget_status, budget_violations = (
                budget.evaluate(invocation.llm_calls, mode=mode) if budget else ("observed", ())
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
                llm_calls=invocation.llm_calls,
                turn_timing=turn_timing,
                llm_budget=budget,
                llm_budget_status=budget_status,
                llm_budget_violations=budget_violations,
                planner_clean=_planner_clean_from_metadata(invocation.route_metadata),
                planner_dirty_reasons=_planner_dirty_reasons_from_metadata(invocation.route_metadata),
                category=scenario.category,
                criticality=scenario.criticality,
                outcome=turn.expectation.expected_outcome if passed else _failed_outcome(errors),
                mutation_id=turn.mutation_id,
                state_snapshot=invocation.state_snapshot,
            )
            results.append(result)
            previous_snapshots[scenario.id] = invocation.state_snapshot
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


def _failed_outcome(errors: tuple[str, ...]) -> ReadinessOutcome:
    joined = " ".join(errors).casefold()
    if "money movement" in joined or "unsafe" in joined:
        return "unsafe_execution"
    if "routing" in joined or "path_shape" in joined or "task types" in joined:
        return "misrouted"
    if "context" in joined:
        return "context_lost"
    if "clarification" in joined:
        return "unnecessary_clarification"
    return "misrouted"
