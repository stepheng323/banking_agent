"""Runtime planner prompt assembly and refresh state."""

from __future__ import annotations

import apps.chat.src.agent.orchestrator.planning.task_planner_prompt_models as prompt_models
from shared.assistant_profile.voice import build_planner_voice_block
from apps.chat.src.agent.orchestrator.planning.task_planner_prompt_compiler import compile_planner_system_prompt


def _build_planner_policy_block() -> str:
    return build_planner_voice_block()


PLANNER_POLICY_BLOCK = _build_planner_policy_block()
PLANNER_PROMPT_BASELINE_RESULT = prompt_models.PlannerPromptBuildResult(
    system_prompt="",
    profile="",
    selected_rule_ids=(),
    selected_bundle_ids=(),
    char_count=0,
)


def build_runtime_planner_system_prompt(
    prompt_input: prompt_models.PlannerPromptBuildInput,
) -> prompt_models.PlannerPromptBuildResult:
    return compile_planner_system_prompt(prompt_input=prompt_input, policy_block=PLANNER_POLICY_BLOCK)


def _refresh_runtime_prompt_baseline() -> None:
    global PLANNER_PROMPT_BASELINE_RESULT
    PLANNER_PROMPT_BASELINE_RESULT = build_runtime_planner_system_prompt(
        prompt_models.PlannerPromptBuildInput(text="", context="None", signals=prompt_models.PlannerPromptSignals())
    )


_refresh_runtime_prompt_baseline()


def refresh_runtime_planner_system_prompt() -> None:
    """Refresh policy block and baseline after policy reload."""
    global PLANNER_POLICY_BLOCK
    PLANNER_POLICY_BLOCK = _build_planner_policy_block()
    _refresh_runtime_prompt_baseline()


__all__ = [
    "PLANNER_POLICY_BLOCK",
    "PLANNER_PROMPT_BASELINE_RESULT",
    "build_runtime_planner_system_prompt",
    "refresh_runtime_planner_system_prompt",
]
