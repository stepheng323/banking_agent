"""Compiler for planner system prompts using typed runtime signals."""

from __future__ import annotations

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_atoms import (
    PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT,
    PLANNER_MIXED_TX_PRECISION_PROMPT,
    PLANNER_RULE_ATOMS,
    PLANNER_RULE_SEMANTIC_GUARD_IDS,
    PLANNER_RUNTIME_COMMON_EXAMPLES,
    PLANNER_RUNTIME_CONTEXT_EXAMPLES,
    PLANNER_RUNTIME_MIXED_TX_EXAMPLES,
    PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES,
    PLANNER_RUNTIME_PROMPT_SUFFIX,
    PLANNER_RUNTIME_SCHEMA_PROMPT,
    PLANNER_RUNTIME_TRANSFER_ONLY_EXAMPLES,
    PLANNER_TRANSFER_ONLY_PRECISION_PROMPT,
    PLANNER_TRANSFER_PRECISION_PROMPT,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import (
    PlannerPromptBuildInput,
    PlannerPromptBuildResult,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_selector import (
    select_prompt_bundles,
    select_rule_ids,
)


def _compile_rule_atoms(rule_ids: tuple[str, ...]) -> str:
    rule_id_section = "## RULE IDS\n" + ",".join(rule_ids)
    semantic_fragments = [
        f"{rule_id.split('_')[0]}:{PLANNER_RULE_ATOMS[rule_id]}"
        for rule_id in rule_ids
        if rule_id in PLANNER_RULE_SEMANTIC_GUARD_IDS
    ]
    if not semantic_fragments:
        return rule_id_section
    return rule_id_section + "\nSEM:" + ";".join(semantic_fragments)


def _coverage_guard_section(expected_executors: tuple[str, ...]) -> str:
    return PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT.format(expected_executors=", ".join(expected_executors))


def compile_planner_system_prompt(
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

    if "transfer_only" in bundles:
        sections.append(PLANNER_TRANSFER_ONLY_PRECISION_PROMPT)
        sections.append(PLANNER_RUNTIME_TRANSFER_ONLY_EXAMPLES)
        profile_parts.extend(["precision_transfer_only", "ex_transfer_only"])
    if "mixed_tx" in bundles:
        sections.append(PLANNER_MIXED_TX_PRECISION_PROMPT)
        sections.append(PLANNER_RUNTIME_MIXED_TX_EXAMPLES)
        profile_parts.extend(["precision_mixed_tx", "ex_mixed_tx"])
    if "money_move" in bundles:
        sections.append(PLANNER_TRANSFER_PRECISION_PROMPT)
        sections.append(PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES)
        profile_parts.extend(["precision_money_move", "ex_money_move"])
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


__all__ = ["compile_planner_system_prompt"]
