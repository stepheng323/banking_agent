"""Unified missing-detail prompt builder for execution."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_recipients import _recipient_prompt_label
from banking.presentation.formatters.missing_detail_prompts import (
    format_missing_details_prompt,
    format_source_repair_prompt,
)
from banking.presentation.formatters.transaction_intent_lines import format_intent_line


def _build_unified_missing_field_prompt(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
    locale: str,
) -> str:
    found_names = []
    missing_prompts = []

    for tid in current_wave:
        task = state.tasks.get(tid)
        if not task or task.stage in TERMINAL_STAGES:
            continue

        if not task.payload.get("recipient_ui_confirmed"):
            name = _recipient_prompt_label(cast(dict[str, Any], task.payload))
            if name and name not in found_names:
                found_names.append(name)

        if agg.has_input_request(tid):
            if p := agg.prompt_for_task(tid):
                missing_prompts.append(p)

    repair_hint = agg.first_source_bank_hint()
    feedback_messages = agg.feedback_messages_for_prompt()

    if repair_hint and feedback_messages:
        intents = []
        for tid in current_wave:
            task = state.tasks.get(tid)
            if not task or task.stage in TERMINAL_STAGES:
                continue
            intents.append(format_intent_line(task.type, task.payload, locale=locale))

        accounts = state.loaded_context.get("transaction_accounts", [])
        prompt_text = format_source_repair_prompt(
            intents=intents,
            failed_hint=repair_hint,
            accounts=accounts,
            locale=locale,
        )
    else:
        prompt_text = format_missing_details_prompt(
            found_names=found_names,
            missing_prompts=missing_prompts,
            feedback_messages=feedback_messages,
            locale=locale,
        )

    for tid in current_wave:
        task = state.tasks.get(tid)
        if not task:
            continue
        name = _recipient_prompt_label(cast(dict[str, Any], task.payload))
        if name and name in found_names:
            task.payload["recipient_ui_confirmed"] = True

    return prompt_text


__all__ = ["_build_unified_missing_field_prompt"]
