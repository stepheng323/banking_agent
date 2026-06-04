"""Batch source-account prompt assembly for execution input interrupts."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from banking.presentation.formatters.transfer_input_prompts import format_batch_transfer_source_prompt
from banking.presentation.i18n.renderer import render_message


def _transfer_tasks_only_missing_source(state: OrchestratorState, agg: ExecutionAccumulator) -> list[str]:
    return [
        tid
        for tid in agg.missing_fields_by_task
        if state.tasks.get(tid)
        and state.tasks[tid].type == "transfer"
        and set(agg.missing_fields_by_task[tid]) == {"source_account_id"}
    ]


def build_batch_source_prompt_if_needed(
    *,
    state: OrchestratorState,
    agg: ExecutionAccumulator,
    locale: str,
) -> str | None:
    transfer_tasks_only_source = _transfer_tasks_only_missing_source(state, agg)
    all_batch_source = len(transfer_tasks_only_source) >= 2 and len(transfer_tasks_only_source) == len(
        agg.missing_fields_by_task
    )
    if not all_batch_source:
        return None

    accounts = state.loaded_context.get("transaction_accounts") or []
    lines = []
    total_amount = 0.0
    amounts = []
    for tid in transfer_tasks_only_source:
        task = state.tasks[tid]
        amt = task.payload.get("amount") or 0.0
        total_amount += amt
        amounts.append(amt)
        if task.payload.get("recipient_ui_confirmed"):
            continue
        label = (
            task.payload.get("recipient_name")
            or task.payload.get("recipient_resolved_name")
            or render_message("orchestrator.finalize.recipient_fallback", locale)
        )
        resolved = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name") or ""
        bank = task.payload.get("recipient_bank_name")
        acc = task.payload.get("recipient_account")
        if bank and acc and resolved:
            lines.append(
                render_message(
                    "orchestrator.execution.recipient_resolved_with_bank",
                    locale,
                    {"label": label, "resolved": resolved, "bank": bank, "account": acc},
                )
            )
        elif resolved:
            lines.append(
                render_message(
                    "orchestrator.execution.recipient_resolved",
                    locale,
                    {"label": label, "resolved": resolved},
                )
            )
        else:
            lines.append(
                render_message(
                    "orchestrator.execution.recipient_resolved_generic",
                    locale,
                    {"label": label},
                )
            )

    prompt_text = format_batch_transfer_source_prompt(
        resolved_lines=lines,
        total_amount=total_amount,
        amounts=amounts,
        accounts=accounts,
        locale=locale,
    )
    for tid in transfer_tasks_only_source:
        state.tasks[tid].payload["recipient_ui_confirmed"] = True
    return prompt_text


__all__ = ["build_batch_source_prompt_if_needed"]
