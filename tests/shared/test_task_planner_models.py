"""Tests for TaskPlanner model wiring."""

from __future__ import annotations

from typing import Any

from shared.services.task_planner import TaskPlanner


class _FakeLLM:
    def __init__(self, name: str) -> None:
        self.name = name

    def with_structured_output(self, schema: Any) -> str:
        return f"{self.name}:{getattr(schema, '__name__', 'unknown')}"


def test_task_planner_uses_dedicated_interrupt_model_when_provided() -> None:
    planner_llm = _FakeLLM("planner")
    interrupt_llm = _FakeLLM("interrupt")

    planner = TaskPlanner(planner_llm=planner_llm, interrupt_llm=interrupt_llm)

    assert planner.structured_planner == "planner:PlannerOutput"
    assert planner.structured_interrupt_router == "interrupt:InterruptRouteDecision"
    assert planner.structured_quoted_replay == "planner:QuotedReplayInterpretation"


def test_task_planner_falls_back_to_planner_model_for_interrupt_router() -> None:
    planner_llm = _FakeLLM("planner")

    planner = TaskPlanner(planner_llm=planner_llm)

    assert planner.structured_interrupt_router == "planner:InterruptRouteDecision"
