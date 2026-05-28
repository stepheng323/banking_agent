"""Shared types for planner context assembly flow."""

from dataclasses import dataclass
from typing import Any

from shared.services.task_planner_prompt_models import PlannerPromptSignals


@dataclass(slots=True)
class PlannerContextBuildResult:
    planner_context: str
    active_intent: str | None
    query_session_snapshot: dict[str, Any] | None
    query_session_source: str | None
    prompt_signals: PlannerPromptSignals
    shortcut_updates: dict[str, Any] | None = None


__all__ = ["PlannerContextBuildResult"]
