"""Compiler for planner system prompts using typed runtime signals."""

from __future__ import annotations

from shared.services.task_planner_prompt_atoms import (
    PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT,
    PLANNER_RULE_ATOMS,
    PLANNER_RUNTIME_COMMON_EXAMPLES,
    PLANNER_RUNTIME_CONTEXT_EXAMPLES,
    PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES,
    PLANNER_RUNTIME_PROMPT_SUFFIX,
    PLANNER_RUNTIME_QUERY_EXAMPLES,
    PLANNER_RUNTIME_SCHEMA_PROMPT,
    PLANNER_TRANSFER_PRECISION_PROMPT,
)
from shared.services.task_planner_prompt_models import (
    PlannerPromptBuildInput,
    PlannerPromptBuildResult,
)
from shared.services.task_planner_prompt_selector import select_prompt_bundles, select_rule_ids


def _compile_rule_atoms(rule_ids: tuple[str, ...]) -> str:
    lines = ["## COMPILED RULE ATOMS"]
    for rule_id in rule_ids:
        lines.append(f"- {rule_id}: {PLANNER_RULE_ATOMS[rule_id]}")
    return "\n".join(lines)


def _coverage_guard_section(expected_executors: tuple[str, ...]) -> str:
    return PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT.format(expected_executors=", ".join(expected_executors))


def build_planner_system_prompt(
    *,
    prompt_input: PlannerPromptBuildInput,
    policy_block: str,
) -> PlannerPromptBuildResult:
    bundles = select_prompt_bundles(prompt_input.signals)
    rule_ids = select_rule_ids(prompt_input.signals)

    sections = [
        policy_block,
        PLANNER_RUNTIME_SCHEMA_PROMPT,
        _compile_rule_atoms(rule_ids),
        PLANNER_RUNTIME_COMMON_EXAMPLES,
    ]
    profile_parts = ["schema", f"rules_{len(rule_ids)}", "ex_common"]

    if "money_move" in bundles:
        sections.append(PLANNER_TRANSFER_PRECISION_PROMPT)
        sections.append(PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES)
        profile_parts.extend(["precision_money_move", "ex_money_move"])
    if "query" in bundles:
        sections.append(PLANNER_RUNTIME_QUERY_EXAMPLES)
        profile_parts.append("ex_query")
    if "context" in bundles:
        sections.append(PLANNER_RUNTIME_CONTEXT_EXAMPLES)
        profile_parts.append("ex_context")
    if "executor_coverage_guard" in bundles and prompt_input.signals.expected_transaction_executors:
        sections.append(_coverage_guard_section(prompt_input.signals.expected_transaction_executors))
        profile_parts.append("guard_exec_cov")

    sections.append(PLANNER_RUNTIME_PROMPT_SUFFIX)

    system_prompt = "\n\n".join(sections)
    return PlannerPromptBuildResult(
        system_prompt=system_prompt,
        profile="+".join(profile_parts),
        selected_rule_ids=rule_ids,
        selected_bundle_ids=bundles,
        char_count=len(system_prompt),
    )


__all__ = ["build_planner_system_prompt"]
