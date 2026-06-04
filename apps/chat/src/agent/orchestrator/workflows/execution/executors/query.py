"""Query task execution handler."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import (
    push_query_followup_referent_frame,
    push_query_surface_frame,
    query_pagination_actionable_payload,
)
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.query_handoff import _next_query_handoff_transfer_task_id
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import _apply_result_patch
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.contracts import FocusedReferent


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

    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "profile": ctx.state.loaded_context.get("profile", {}),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "language": _state_locale(ctx.state),
        "inbound_message_id": ctx.state.last_message_id,
        "turn_id": ctx.state.last_message_id,
        "stashed_query_session": ctx.state.stashed_query_session,
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
    if ctx.state.stashed_query_session is not None:
        ctx.accumulator.set_update("stashed_query_session", None)
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
        task.stage = TaskStage.COMPLETED
        if result.response:
            task.payload["result"] = result.response
            pagination_payload = query_pagination_actionable_payload(ctx, result)
            if pagination_payload:
                ctx.accumulator.add_outbox(
                    {
                        "type": "say",
                        "text": result.response,
                        "actionable_payload": pagination_payload,
                    }
                )
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
            transfer_payload.setdefault("message", ctx.state.last_message_text or "Resend the selected transaction")
            transfer_payload.setdefault("skip_extraction", True)

            tasks = cast(dict[str, Any], ctx.accumulator.updates.get("tasks", ctx.state.tasks))
            transfer_task_id = _next_query_handoff_transfer_task_id(tasks)
            tasks[transfer_task_id] = TaskSpec(
                id=transfer_task_id,
                type="transfer",
                stage=TaskStage.DRAFT,
                payload=transfer_payload,
            )
            ctx.accumulator.set_update("tasks", tasks)

            waves = list(cast(list[list[str]], ctx.accumulator.updates.get("waves", ctx.state.waves)))
            insert_index = min(ctx.state.current_wave_index + 1, len(waves))
            waves.insert(insert_index, [transfer_task_id])
            ctx.accumulator.set_update("waves", waves)

            if not result.response:
                ctx.accumulator.say("Okay. I will resend that transfer now.")

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        if result.response:
            ctx.accumulator.add_prompt(result.response, task_id)
            ctx.accumulator.add_missing_fields(task_id, ["clarification"])

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or render_message(
            "orchestrator.error.query_processing_failed",
            _state_locale(ctx.state),
        )
        ctx.accumulator.say(result.response or render_message("query.error.general", _state_locale(ctx.state)))

    if result.outcome in (TransactionOutcome.OK, TransactionOutcome.NEEDS_INPUT):
        stack = list(ctx.state.session_stack)
        if handoff_payload and result.outcome == TransactionOutcome.OK:
            if stack and stack[-1].domain == "query":
                stack.pop()
        else:
            if stack and stack[-1].domain == "query":
                stack[-1].state = "WAITING_FOR_INPUT" if result.outcome == TransactionOutcome.NEEDS_INPUT else "RUNNING"
            else:
                new_session = ActiveSession(
                    domain="query",
                    state="WAITING_FOR_INPUT" if result.outcome == TransactionOutcome.NEEDS_INPUT else "RUNNING",
                    interrupt_policy="ALLOW",
                    resume_hint={"task_id": task_id},
                )
                stack.append(new_session)

        ctx.accumulator.set_update("session_stack", stack)


__all__ = ["QueryTaskExecutor"]
