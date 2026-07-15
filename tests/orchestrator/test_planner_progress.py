from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import (
    PlannerPromptSignals,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import (
    PlannerPlanResult,
    PlannerQualityReport,
)
from apps.chat.src.agent.orchestrator.workflows.planner.execution_flow import (
    _plan_tasks_with_optional_quality,
)
from shared.types.planner import PlannerOutput


class _Tracker:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.stage_key: str | None = None
        self.metadata: dict[str, object] | None = None

    async def set_stage(self, stage_key: str, *, stage_metadata: dict[str, object] | None = None) -> None:
        self.calls.append("progress")
        self.stage_key = stage_key
        self.metadata = stage_metadata


class _Planner:
    def __init__(self, calls: list[str], result: PlannerPlanResult) -> None:
        self.calls = calls
        self.result = result

    async def plan_tasks_with_quality(self, *_args: object, **_kwargs: object) -> PlannerPlanResult:
        self.calls.append("planner")
        return self.result


async def test_planner_progress_starts_immediately_before_the_structured_plan_call() -> None:
    calls: list[str] = []
    output = PlannerOutput(primary_intent="transfer")
    result = PlannerPlanResult(
        raw_output=output,
        planner_output=output,
        quality_report=PlannerQualityReport(),
    )
    tracker = _Tracker(calls)

    actual = await _plan_tasks_with_optional_quality(
        _Planner(calls, result),  # type: ignore[arg-type]
        "2348012345678",
        "send 10k to Tolu",
        planner_context="",
        prompt_signals=PlannerPromptSignals(),
        progress_tracker=tracker,
        progress_metadata={"target_domain": "transfer"},
    )

    assert actual is result
    assert calls == ["progress", "planner"]
    assert tracker.stage_key == "planner.planning"
    assert tracker.metadata == {"target_domain": "transfer"}
