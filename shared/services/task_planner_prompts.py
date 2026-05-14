"""Planner prompt compilation surface (clean-break API)."""

from __future__ import annotations

from shared.assistant_profile.voice import build_planner_voice_block
from shared.services.task_planner_prompt_atoms import PLANNER_RULE_ATOMS
from shared.services.task_planner_prompt_compiler import build_planner_system_prompt as _build_compiled_prompt
from shared.services.task_planner_prompt_models import (
    PlannerPromptBuildInput,
    PlannerPromptBuildResult,
    PlannerPromptSignals,
)


def _build_planner_policy_block() -> str:
    return build_planner_voice_block()


PLANNER_POLICY_BLOCK = _build_planner_policy_block()
PLANNER_PROMPT_BASELINE_RESULT = PlannerPromptBuildResult(
    system_prompt="",
    profile="",
    selected_rule_ids=(),
    selected_bundle_ids=(),
    char_count=0,
)


def build_planner_system_prompt(prompt_input: PlannerPromptBuildInput) -> PlannerPromptBuildResult:
    return _build_compiled_prompt(prompt_input=prompt_input, policy_block=PLANNER_POLICY_BLOCK)


def _refresh_runtime_prompt_baseline() -> None:
    global PLANNER_PROMPT_BASELINE_RESULT
    PLANNER_PROMPT_BASELINE_RESULT = build_planner_system_prompt(
        PlannerPromptBuildInput(text="", context="None", signals=PlannerPromptSignals())
    )


_refresh_runtime_prompt_baseline()


def refresh_planner_system_prompt() -> None:
    """Refresh policy block and baseline after policy reload."""
    global PLANNER_POLICY_BLOCK
    PLANNER_POLICY_BLOCK = _build_planner_policy_block()
    _refresh_runtime_prompt_baseline()


__all__ = [
    "PLANNER_RULE_ATOMS",
    "PLANNER_PROMPT_BASELINE_RESULT",
    "PlannerPromptSignals",
    "PlannerPromptBuildInput",
    "PlannerPromptBuildResult",
    "build_planner_system_prompt",
    "refresh_planner_system_prompt",
]
