"""Input-interrupt prompt assembly for execution."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompt_batch_source import (
    build_batch_source_prompt_if_needed,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompt_filtering import (
    apply_missing_field_prompt_filters,
    tasks_needing_basic_fields,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts_focused import (
    _build_focused_missing_field_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts_unified import (
    _build_unified_missing_field_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_options import (
    _build_show_options_entry,
    _compact_prompt_for_options,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_queue import (
    _append_queued_notice,
    _queued_transaction_tasks_for_focus,
)


def _build_missing_field_interrupt_updates(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
    locale: str,
) -> dict[str, Any]:
    apply_missing_field_prompt_filters(current_wave=current_wave, agg=agg)

    prompt_text = build_batch_source_prompt_if_needed(state=state, agg=agg, locale=locale)
    if prompt_text is None:
        tasks_needing_basic = tasks_needing_basic_fields(agg)
        focused_tid = None
        if tasks_needing_basic:
            for tid in current_wave:
                if tid in tasks_needing_basic:
                    focused_tid = tid
                    break

        if focused_tid is not None:
            return _build_focused_missing_field_updates(
                state=state,
                current_wave=current_wave,
                agg=agg,
                locale=locale,
                focused_tid=focused_tid,
            )

        prompt_text = _build_unified_missing_field_prompt(
            state=state,
            current_wave=current_wave,
            agg=agg,
            locale=locale,
        )

    fallback_options_entry: dict[str, Any] | None = None
    fallback_queue_meta: dict[str, Any] | None = None
    if len(agg.missing_fields_by_task) == 1:
        task_id = next(iter(agg.missing_fields_by_task.keys()))
        queued_tasks = _queued_transaction_tasks_for_focus(
            state=state,
            current_wave=current_wave,
            focused_tid=task_id,
        )
        prompt_text, fallback_queue_meta = _append_queued_notice(
            prompt_text=prompt_text,
            queued_tasks=queued_tasks,
            locale=locale,
        )
        details = agg.details_by_task.get(task_id)
        focused_missing_fields = agg.missing_fields_by_task.get(task_id, [])
        fallback_options_entry = _build_show_options_entry(
            details=details,
            prompt_text=prompt_text,
            task_id=task_id,
            focused_missing_fields=focused_missing_fields,
        )
        if fallback_options_entry:
            prompt_text = _compact_prompt_for_options(prompt_text)
            fallback_options_entry["title"] = prompt_text

    interrupt = PendingInterrupt(
        kind="input",
        task_ids=list(agg.missing_fields_by_task.keys()),
        fields_by_task=agg.missing_fields_by_task,
        prompt=prompt_text,
    )
    fallback_outbox_entries: list[dict[str, Any]] = [{"type": "say", "text": prompt_text}]
    if fallback_options_entry:
        fallback_outbox_entries = [fallback_options_entry]
    fallback_outbox_entries[0]["prompt_kind"] = "pending_input"
    if fallback_queue_meta is not None:
        fallback_outbox_entries[0]["queue"] = fallback_queue_meta
    agg.set_interrupt_outbox(interrupt, _with_policy_notice(state, fallback_outbox_entries))
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = [
    "_build_missing_field_interrupt_updates",
]
