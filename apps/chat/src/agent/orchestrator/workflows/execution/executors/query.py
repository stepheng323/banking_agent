"""Query task execution handler."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.query_surface import (
    build_query_context_for_worker,
    build_query_session_snapshot_from_surface,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import (
    push_query_followup_referent_frame,
    push_query_surface_frame,
    query_pagination_actionable_payload,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
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
from banking.transactions.query.models.domain import QueryAnswerStrategy, QueryIntent, QueryResult
from banking.transactions.query.presentation.formatter import QueryFormatter
from shared.messaging.body_blocks import MessageDocument
from shared.types.read import ReadRequest, ReadResult, ResponseShape, normalize_read_request


class QueryTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_query_task(task, task_id, ctx)


def _compact_query_session_patch(patch: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only pending-query compatibility fields for orchestrator checkpoint state."""
    if not isinstance(patch, dict):
        return None
    allowed = {
        "session_active",
        "query_contract",
        "query_result",
        "query_frames",
        "pending_clarification",
        "current_page",
        "page_size",
        "show_expanded",
        "timestamp",
        "account_id",
        "account_ids",
        "cache_fingerprint",
        "cache_scope_fingerprint",
        "cache_window_start",
        "cache_window_end",
    }
    compact: dict[str, Any] = {}
    for key in allowed:
        value = patch.get(key)
        if value is None:
            continue
        if hasattr(value, "model_dump"):
            compact[key] = value.model_dump(mode="json")
        elif isinstance(value, list):
            compact[key] = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
        else:
            compact[key] = value
    return compact or None


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
        "pending_query_clarification": turn.pending_query_clarification,
        "progress_tracker": ctx.dependencies.progress_tracker,
        "stashed_sessions": turn.stashed_sessions,
        "recent_query_context": ctx.state.recent_query_context,
        **build_query_context_for_worker(ctx.state),
    }

    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
        ),
    )

    _apply_result_patch(task, result)
    if turn.has_pending_query_clarification:
        ctx.accumulator.clear_pending_query_clarification()

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
        if isinstance(query_result, QueryResult) and result.read_result is None:
            read_request = normalize_read_request(task.payload)
            if read_request is None:
                if query_result.answer_strategy == QueryAnswerStrategy.TRANSACTION_LIST:
                    shape: ResponseShape = "surface_paginated" if query_result.has_more else "surface_list"
                elif query_result.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER:
                    shape = "fact_value"
                else:
                    shape = "surface_list"
                read_request = ReadRequest(subject="transaction", response_shape=shape)
            item_count = len(query_result.items or [])
            result.read_result = ReadResult(
                request=read_request,
                total_count=item_count + (1 if query_result.has_more else 0),
                returned_count=min(item_count, read_request.page_size),
                has_next=query_result.has_more,
                has_previous=read_request.offset > 0,
            )

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

        if followup_referent and not handoff_payload:
            push_query_followup_referent_frame(ctx, followup_referent)

        if result.patch and isinstance(result.patch, dict):
            push_query_surface_frame(ctx, result.patch.get("query_result"))
            if result.patch.get("query_result") is not None:
                ctx.accumulator.set_recent_query_context(None)

        if handoff_payload:
            transfer_payload = dict(handoff_payload)
            transfer_payload.setdefault("action", "send_money")
            transfer_payload.setdefault("instruction", "Resend the selected transaction")
            transfer_payload.setdefault("message", turn.last_message_text_or("Resend the selected transaction"))
            transfer_payload.setdefault("skip_extraction", True)

            tasks = dict(ctx.accumulator.get_tasks(task_map(ctx.state)))
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
        compact_session = _compact_query_session_patch(result.patch)
        if compact_session is not None:
            ctx.accumulator.set_pending_query_clarification(compact_session)
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
        if (handoff_payload and result.outcome == TransactionOutcome.OK) or (
            isinstance(result.patch, dict) and result.patch.get("session_active") is False
        ):
            if isinstance(result.patch, dict) and result.patch.get("session_active") is False:
                raw_surface = context_data.get("active_query_surface")
                try:
                    frame = ContextFrame.model_validate(raw_surface) if isinstance(raw_surface, dict) else None
                except Exception:
                    frame = None
                snapshot = (
                    build_query_session_snapshot_from_surface(
                        frame,
                        context_frames=list(context_surface(ctx.state).frames),
                    )
                    if frame is not None
                    else None
                )
                if snapshot is not None:
                    snapshot["session_active"] = False
                    ctx.accumulator.set_recent_query_context(
                        {"session": snapshot, "closure_turn_id": turn.last_message_id, "remaining_turns": 2}
                    )
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
    if not isinstance(result.patch, dict) or result.patch.get("suppress_body_blocks"):
        return None

    query_result = result.patch.get("query_result")
    if isinstance(query_result, dict):
        try:
            query_result = QueryResult.model_validate(query_result)
        except ValueError:
            return None
    if not isinstance(query_result, QueryResult):
        return None
    if not query_result.items:
        return None
    if query_result.answer_strategy != QueryAnswerStrategy.TRANSACTION_LIST:
        query_contract = query_result.query_contract
        if query_contract is None or query_contract.intent not in {
            QueryIntent.TRANSACTION_LIST,
            QueryIntent.TRANSACTION_SEARCH,
        }:
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
