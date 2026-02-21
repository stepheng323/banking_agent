from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_account_task,
    handle_airtime_task,
    handle_beneficiary_task,
    handle_data_task,
    handle_faq_task,
    handle_orchestrator_task,
    handle_query_task,
    handle_support_task,
    handle_transfer_task,
)
from apps.core.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskStage,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.formatters.accounts import format_accounts_list
from shared.formatters.prompts import (
    format_auth_reason,
    format_batch_transfer_source_prompt,
    format_missing_details_prompt,
    format_single_transfer_recipient_prompt,
    format_source_repair_prompt,
)
from shared.formatters.transaction_summary import format_batch_transfer_summary, format_intent_line
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _with_policy_notice(state: OrchestratorState, outbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend policy notice once per turn when present."""
    if not state.policy_notice:
        return outbox
    logger.info("policy_notice_injected")
    return [{"type": "say", "text": state.policy_notice}, *outbox]


async def advance_wave(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Execution Node.

    Iterates through tasks in current wave.
    Invokes Domain Workers.
    Aggregates outcomes and sets PendingInterrupt if blocked.
    """
    if not state.waves or state.current_wave_index >= len(state.waves):
        logger.info("advance_wave_skip", index=state.current_wave_index, count=len(state.waves))
        return {}

    current_wave = state.waves[state.current_wave_index]
    logger.info(
        "advance_wave", index=state.current_wave_index, tasks=current_wave, context_frames_len=len(state.context_frames)
    )

    # [SAFETY] If pending_interrupt is already set (e.g. valid restoration), do NOT execute tasks.
    # Return it to force graph to stop/route correctly.
    if state.pending_interrupt:
        logger.info("advance_wave_blocked_by_interrupt", kind=state.pending_interrupt.kind)
        return {"pending_interrupt": state.pending_interrupt}

    services = config["configurable"].get("services") or {}
    agg = ExecutionAggregation(state.tasks)

    ctx = ExecutionContext(
        state=state,
        config=config,
        services=services,
        current_wave_len=len(current_wave),
        agg=agg,
    )
    locale = (state.loaded_context or {}).get("language", "en")

    handlers = {
        "transfer": handle_transfer_task,
        "account": handle_account_task,
        "beneficiary": handle_beneficiary_task,
        "airtime": handle_airtime_task,
        "query": handle_query_task,
        "data": handle_data_task,
        "faq": handle_faq_task,
        "support": handle_support_task,
        "orchestrator": handle_orchestrator_task,
    }

    for task_id in current_wave:
        task = state.tasks.get(task_id)
        if not task:
            continue

        if task.stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
            continue

        handler = handlers.get(task.type)
        if not handler:
            continue

        await handler(task, task_id, ctx)

    if agg.missing_fields_by_task:
        # [Prioritized Prompting]
        # If ANY task needs basic details (beneficiary, amount, etc.), suppress "Execution" prompts (Source/PIN)
        # for ALL tasks. This prevents confusing parallel prompts like "Select Account" + "Who is Dad?".

        execution_fields = {"source_account_id", "pin", "confirmation_summary"}

        has_basic_blocker = False
        for fields in agg.missing_fields_by_task.values():
            if any(f not in execution_fields for f in fields):
                has_basic_blocker = True
                break

        if has_basic_blocker:
            # Suppress tasks that are ONLY waiting for execution fields
            suppressed_tasks = []
            for tid, fields in agg.missing_fields_by_task.items():
                if all(f in execution_fields for f in fields):
                    suppressed_tasks.append(tid)

            for tid in suppressed_tasks:
                del agg.missing_fields_by_task[tid]
                logger.info("suppressed_execution_prompt", task_id=tid, reason="basic_blocker_active")

        # [Batch transfer] If ALL blocked tasks are transfer and ONLY need source_account_id,
        # ask once for the whole batch (one "Which account?" prompt) instead of per-task.
        transfer_tasks_only_source = [
            tid
            for tid in agg.missing_fields_by_task
            if state.tasks.get(tid)
            and state.tasks[tid].type == "transfer"
            and set(agg.missing_fields_by_task[tid]) == {"source_account_id"}
        ]
        all_batch_source = len(transfer_tasks_only_source) >= 2 and len(transfer_tasks_only_source) == len(
            agg.missing_fields_by_task
        )

        if all_batch_source:
            # [Option C] Batch funding: only newly resolved in prompt, then amount + account list.
            accounts = state.loaded_context.get("accounts") or []
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
        else:
            # [One-at-a-time] When multiple tasks need basic details, focus on one per turn.
            execution_fields = {"source_account_id", "pin", "confirmation_summary"}
            tasks_needing_basic = [
                tid
                for tid in agg.missing_fields_by_task
                if any(f not in execution_fields for f in agg.missing_fields_by_task[tid])
            ]
            focused_tid = None
            if tasks_needing_basic:
                for tid in current_wave:
                    if tid in tasks_needing_basic:
                        focused_tid = tid
                        break

            if focused_tid is not None:
                # One-at-a-time: prompt for one recipient only; only that task gets next message.
                focused_task = state.tasks[focused_tid]
                focused_name = (
                    focused_task.payload.get("recipient_name")
                    or focused_task.payload.get("recipient_resolved_name")
                    or "this recipient"
                )
                just_resolved_tid = None
                just_resolved_name = None
                just_resolved_bank = None
                if state.last_interrupt and state.last_interrupt.task_ids:
                    for tid in state.last_interrupt.task_ids:
                        if tid not in agg.missing_fields_by_task and state.tasks.get(tid):
                            just_resolved_tid = tid
                            rt = state.tasks[just_resolved_tid]
                            just_resolved_name = rt.payload.get("recipient_resolved_name") or rt.payload.get(
                                "recipient_name"
                            )
                            just_resolved_bank = rt.payload.get("recipient_bank_name")
                            break

                found_names = []
                if just_resolved_tid is None:
                    for tid in current_wave:
                        task = state.tasks.get(tid)
                        if not task or task.stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
                            continue
                        if tid not in agg.missing_fields_by_task:
                            if name := task.payload.get("recipient_resolved_name") or task.payload.get(
                                "recipient_name"
                            ):
                                if name not in found_names:
                                    found_names.append(name)

                prompt_text = format_single_transfer_recipient_prompt(
                    focused_name=focused_name,
                    just_resolved_name=just_resolved_name,
                    just_resolved_bank=just_resolved_bank,
                    found_names=found_names,
                    locale=locale,
                )

                # Mark recipients we mentioned as announced (found_names + just_resolved)
                for tid in current_wave:
                    task = state.tasks.get(tid)
                    if not task:
                        continue
                    if tid == focused_tid:
                        continue
                    name = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")
                    if name and (tid not in agg.missing_fields_by_task or tid == just_resolved_tid):
                        task.payload["recipient_ui_confirmed"] = True
                interrupt = PendingInterrupt(
                    kind="input",
                    task_ids=[focused_tid],
                    fields_by_task={focused_tid: agg.missing_fields_by_task[focused_tid]},
                    prompt=prompt_text,
                )
                return {
                    "pending_interrupt": interrupt,
                    "tasks": state.tasks,
                    "outbox": _with_policy_notice(state, [{"type": "say", "text": prompt_text}]),
                    "policy_notice": None,
                }

            # [UX] Smart Unified Prompt (Found X, Missing Y) — multiple blockers or no single focus
            found_names = []
            missing_prompts = []

            for tid in current_wave:
                task = state.tasks.get(tid)
                if not task or task.stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
                    continue

                # Collect names for "I found X" (only if hasn't been announced to UI yet)
                if not task.payload.get("recipient_ui_confirmed"):
                    name = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")
                    if name and name not in found_names:
                        found_names.append(name)

                # Collect missing prompts
                if tid in agg.missing_fields_by_task:
                    if p := agg.prompts_by_task.get(tid):
                        missing_prompts.append(p)

            # [Source Repair Flow]
            # If tasks share a failed source bank hint, use the detailed repair UI
            repair_hint = None
            if agg.source_bank_hints:
                # Use the most common hint (or first one)
                repair_hint = agg.source_bank_hints[0]

            if repair_hint and agg.feedback_messages:
                intents = []
                for tid in current_wave:
                    task = state.tasks.get(tid)
                    if not task or task.stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
                        continue
                    intents.append(format_intent_line(task.type, task.payload, locale=locale))

                accounts = state.loaded_context.get("accounts", [])
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
                    feedback_messages=agg.feedback_messages,
                    locale=locale,
                )

            # Mark resolved tasks that were mentioned in the prompt
            for tid in current_wave:
                task = state.tasks.get(tid)
                if not task:
                    continue
                name = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")
                if name and name in found_names:
                    task.payload["recipient_ui_confirmed"] = True

        interrupt = PendingInterrupt(
            kind="input",
            task_ids=list(agg.missing_fields_by_task.keys()),
            fields_by_task=agg.missing_fields_by_task,
            prompt=prompt_text,
        )
        return {
            "pending_interrupt": interrupt,
            "tasks": state.tasks,
            "outbox": _with_policy_notice(state, [{"type": "say", "text": prompt_text}]),
            "policy_notice": None,
        }

    updates = agg.updates
    if state.policy_notice:
        existing = updates.get("outbox", [])
        updates["outbox"] = _with_policy_notice(state, existing)
        updates["policy_notice"] = None

    # [Confirmation Aggregation]
    if agg.needs_confirm_tasks:
        total_amount = 0.0
        source_account_info = None
        summaries = []

        for tid in agg.needs_confirm_tasks:
            task = state.tasks[tid]
            t_payload = task.payload.get("confirmation") or {}
            snap = t_payload.get("snapshot") or {}
            total_amount += snap.get("amount", 0)

            # Extract source info from the first task (assume batch shares source)
            if source_account_info is None:
                bank = snap.get("sourceBank") or snap.get("source_bank")
                acc = snap.get("sourceAccount") or snap.get("source_account")
                if bank and acc:
                    last4 = str(acc)[-4:]
                    source_account_info = render_message(
                        "orchestrator.execution.source_account_info",
                        locale,
                        {"bank": bank, "last4": last4},
                    )

            if s := t_payload.get("summary"):
                summaries.append(s)

        if len(agg.needs_confirm_tasks) == 1:
            parts = [summaries[0]]
            if source_account_info:
                parts.append("")
                parts.append(source_account_info)
            summ = "\n".join(parts)
        else:
            summ = format_batch_transfer_summary(
                num_transfers=len(agg.needs_confirm_tasks),
                total_amount=total_amount,
                source_account_info=source_account_info,
                summaries=summaries,
                locale=locale,
            )
        first_task_payload = state.tasks[agg.needs_confirm_tasks[0]].payload.get("confirmation", {})
        snap = first_task_payload.get("snapshot", {})
        update_msg = first_task_payload.get("update_message")

        interrupt = PendingInterrupt(
            kind="confirmation",
            task_ids=agg.needs_confirm_tasks,
        )

        outbox = []
        if update_msg:
            outbox.append({"type": "say", "text": update_msg})

        outbox.append(
            {
                "type": "request_confirmation",
                "task_ids": agg.needs_confirm_tasks,
                "summary": summ,
                "snapshot": snap,
                "idempotency_key": state.tasks[agg.needs_confirm_tasks[0]].payload.get(
                    "idempotency_key",
                    "unknown",
                ),
            }
        )

        updates["outbox"] = outbox
        updates["pending_interrupt"] = interrupt
        return updates

    if agg.needs_auth_tasks:
        first_task = state.tasks[agg.needs_auth_tasks[0]]
        summ = first_task.payload.get("confirmation", {}).get(
            "summary",
            render_message("orchestrator.execution.pin_prompt_default", locale),
        )
        snap = first_task.payload.get("confirmation", {}).get("snapshot", {})

        idem_key = first_task.payload.get("idempotency_key", "no-key")

        task_type = first_task.type
        reason = format_auth_reason(task_type, locale=locale)

        interrupt = PendingInterrupt(kind="auth", task_ids=agg.needs_auth_tasks, auth_method="pin", prompt=summ)
        updates["outbox"] = [
            {
                "type": "auth_request",
                "method": "pin",
                "task_ids": agg.needs_auth_tasks,
                "idempotency_key": idem_key,
                "header": reason,
                "summary": summ,
            }
        ]
        updates["pending_interrupt"] = interrupt
        return updates

    all_terminal = True
    for task_id in current_wave:
        task = state.tasks.get(task_id)
        if not task:
            continue
        if task.stage not in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
            all_terminal = False
            break

    if all_terminal:
        updates["current_wave_index"] = state.current_wave_index + 1

    return updates
