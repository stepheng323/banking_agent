"""Canonical route fixtures for direct orchestrator-node tests."""

from apps.chat.src.agent.orchestrator.models.turn_directive import (
    TurnDirective,
    TurnNextStep,
    TurnOutcomeKind,
    build_turn_directive,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import (
    PlannerPlanResult,
    PlannerQualityReport,
)
from shared.types.planner import PlannerOutput


def planner_test_result(output: PlannerOutput) -> PlannerPlanResult:
    """Build the canonical planner boundary result for orchestrator test doubles."""

    return PlannerPlanResult(
        raw_output=output,
        planner_output=output,
        quality_report=PlannerQualityReport(),
    )


def execution_test_directive() -> TurnDirective:
    """Represent a task plan committed immediately before execution."""
    return build_turn_directive(
        owner="planner",
        decision="test_task_dispatch",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        next_step=TurnNextStep.ADVANCE,
        source="test_fixture",
        path_shape="test_execution",
    )


def finalize_test_directive() -> TurnDirective:
    """Represent completed execution committed immediately before finalization."""
    return build_turn_directive(
        owner="planner",
        decision="test_task_dispatch",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        next_step=TurnNextStep.FINALIZE,
        source="test_fixture",
        path_shape="test_finalize",
    )


__all__ = ["execution_test_directive", "finalize_test_directive", "planner_test_result"]
