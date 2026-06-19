"""Query task execution handler."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import (
    push_query_followup_referent_frame,
    push_query_surface_frame,
    query_pagination_actionable_payload,
)
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.query_handoff import _next_query_handoff_transfer_task_id
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import _apply_result_patch
from apps.chat.src.agent.orchestrator.workflows.execution.session_stack import (
    pop_active_session,
    upsert_active_session,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import task_map
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    complete_task,
    fail_task,
    set_task_payload_value,
    set_task_stage,
)
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import next_wave_index, wave_list
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.contracts import FocusedReferent
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.presentation.formatter import QueryFormatter
from shared.messaging.body_blocks import MessageDocument


class QueryTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_query_task(task, task_id, ctx)


async def _execute_query_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    worker = _get_worker(
        ctx.services,
        "query",
        task,
        log_key="query_worker_missing",
        error_message=render_message("orchestrator.error.query_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    context = loaded_context(ctx.state)
    turn = turn_metadata(ctx.state)
    context_data = {
        "phone_number": turn.phone_number,
        "user_id": context.user_id,
        "profile": context.profile,
        "accounts": context.accounts,
        "language": _state_locale(ctx.state),
        "inbound_message_id": turn.last_message_id,
        "turn_id": turn.last_message_id,
        "stashed_query_session": turn.stashed_query_session,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }

    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
        ),
    )

    _apply_result_patch(task, result)
    if turn.has_stashed_query_session:
        ctx.accumulator.clear_stashed_query_session()
    handoff_payload = None
    followup_referent: FocusedReferent | dict[str, Any] | None = None
    if result.patch and isinstance(result.patch, dict):
        candidate = result.patch.get("query_transfer_handoff")
        if isinstance(candidate, dict):
            handoff_payload = candidate
        query_result = result.patch.get("query_result")
        referent_candidate = getattr(query_result, "followup_referent", None)
        if isinstance(referent_candidate, FocusedReferent | dict):
            followup_referent = referent_candidate

    if result.outcome == TransactionOutcome.OK:
        complete_task(task)
        if result.response:
            set_task_payload_value(task, "result", result.response)
            pagination_payload = query_pagination_actionable_payload(ctx, result)
            body_blocks = _query_response_body_blocks(ctx, result)
            if pagination_payload:
                outbox_entry: dict[str, Any] = {
                    "type": "say",
                    "text": result.response,
                    "actionable_payload": pagination_payload,
                }
                if body_blocks:
                    outbox_entry["body_blocks"] = body_blocks
                ctx.accumulator.add_outbox(outbox_entry)
            elif body_blocks:
                ctx.accumulator.add_outbox({"type": "say", "text": result.response, "body_blocks": body_blocks})
            else:
                ctx.accumulator.say(result.response)

        if result.patch and isinstance(result.patch, dict):
            push_query_surface_frame(ctx, result.patch.get("query_result"))

        if followup_referent and not handoff_payload:
            push_query_followup_referent_frame(ctx, followup_referent)

        if handoff_payload:
            transfer_payload = dict(handoff_payload)
            transfer_payload.setdefault("action", "send_money")
            transfer_payload.setdefault("instruction", "Resend the selected transaction")
            transfer_payload.setdefault("message", turn.last_message_text_or("Resend the selected transaction"))
            transfer_payload.setdefault("skip_extraction", True)

            tasks = ctx.accumulator.get_tasks(task_map(ctx.state))
            transfer_task_id = _next_query_handoff_transfer_task_id(tasks)
            tasks[transfer_task_id] = TaskSpec(
                id=transfer_task_id,
                type="transfer",
                stage=TaskStage.DRAFT,
                payload=transfer_payload,
            )
            ctx.accumulator.set_tasks(tasks)

            waves = list(ctx.accumulator.get_waves(wave_list(ctx.state)))
            insert_index = min(next_wave_index(ctx.state), len(waves))
            waves.insert(insert_index, [transfer_task_id])
            ctx.accumulator.set_waves(waves)

            if not result.response:
                ctx.accumulator.say("Okay. I will resend that transfer now.")

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        set_task_stage(task, TaskStage.EXTRACTED)
        if result.response:
            ctx.accumulator.add_prompt(result.response, task_id)
            ctx.accumulator.add_missing_fields(task_id, ["clarification"])

    elif result.outcome == TransactionOutcome.FAILED:
        fail_task(
            task,
            result.error
            or render_message(
                "orchestrator.error.query_processing_failed",
                _state_locale(ctx.state),
            ),
        )
        ctx.accumulator.say(result.response or render_message("query.error.general", _state_locale(ctx.state)))

    if result.outcome in (TransactionOutcome.OK, TransactionOutcome.NEEDS_INPUT):
        if handoff_payload and result.outcome == TransactionOutcome.OK:
            pop_active_session(ctx, domain="query")
        else:
            upsert_active_session(
                ctx,
                domain="query",
                state="WAITING_FOR_INPUT" if result.outcome == TransactionOutcome.NEEDS_INPUT else "RUNNING",
                interrupt_policy="ALLOW",
                task_id=task_id,
            )


def _query_response_body_blocks(ctx: ExecutionTurnContext, result: TransactionResult) -> MessageDocument | None:
    if not isinstance(result.patch, dict):
        return None

    query_result = result.patch.get("query_result")
    if isinstance(query_result, dict):
        try:
            query_result = QueryResult.model_validate(query_result)
        except ValueError:
            return None
    if not isinstance(query_result, QueryResult):
        return None

    try:
        current_page = int(result.patch.get("current_page") or 0)
    except (TypeError, ValueError):
        current_page = 0

    return QueryFormatter.format_blocks(
        query_result,
        current_page=current_page,
        has_more=bool(query_result.has_more),
        locale=_state_locale(ctx.state),
    )


__all__ = ["QueryTaskExecutor"]
