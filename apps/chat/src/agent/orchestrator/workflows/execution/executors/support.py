from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import (
    push_read_result_frame,
    push_support_ticket_list_frame,
)
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import TransferTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.progress import enter_task_progress
from apps.chat.src.agent.orchestrator.workflows.execution.query_handoff import _next_query_handoff_transfer_task_id
from apps.chat.src.agent.orchestrator.workflows.execution.session_stack import (
    pop_active_session,
    upsert_active_session,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import task_map
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    complete_task,
    fail_task,
    replace_task_payload,
    set_task_confirmation,
    set_task_payload_value,
    set_task_stage,
    set_task_type,
)
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import next_wave_index, wave_list
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.intent.routing_signals import looks_like_transaction_replay_modifier_request
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import FAQOutcome, FAQResult, SupportOutcome, SupportResult
from shared.types.read import ReadResult, normalize_read_request
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FAQTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_faq_task(task, task_id, ctx)


class SupportTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_support_task(task, task_id, ctx)


async def _execute_faq_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    worker = _get_worker(
        ctx.services,
        "faq",
        task,
        log_key="faq_worker_missing",
        error_message=render_message("orchestrator.error.faq_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    turn = turn_metadata(ctx.state)
    user_msg = turn.last_message_text
    context_data = {
        "phone_number": turn.phone_number,
        "language": _state_locale(ctx.state),
        "stashed_sessions": turn.stashed_sessions,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }

    result = cast(
        FAQResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
        ),
    )

    if result.outcome == FAQOutcome.OK:
        if result.should_route_to_support:
            logger.info("faq_task_delegating_to_support", task_id=task_id)
            set_task_type(task, "support")
            set_task_payload_value(task, "action", "handle_request")
            await _execute_support_task(task, task_id, ctx)
            return

        complete_task(task)
        ctx.accumulator.say(result.response)
    elif result.outcome == FAQOutcome.FAILED:
        fail_task(
            task,
            result.error
            or render_message(
                "orchestrator.error.faq_failed",
                _state_locale(ctx.state),
            ),
        )
        ctx.accumulator.say(render_message("faq.info_trouble", _state_locale(ctx.state)))


async def _execute_support_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    turn = turn_metadata(ctx.state)
    user_msg = turn.last_message_text
    if looks_like_transaction_replay_modifier_request(user_msg):
        logger.info("support_task_replay_modifier_rerouted_to_transfer", task_id=task_id)
        set_task_type(task, "transfer")
        replace_task_payload(
            task,
            {
                "message": user_msg,
                "instruction": user_msg,
            },
        )
        await TransferTaskExecutor().execute(task, task_id, ctx)
        return

    worker = _get_worker(
        ctx.services,
        "support",
        task,
        log_key="support_worker_missing",
        error_message=render_message("orchestrator.error.support_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    context = loaded_context(ctx.state)
    context_data = {
        "phone_number": turn.phone_number,
        "channel": turn.channel,
        "channel_identity": turn.channel_identity,
        "user_id": context.user_id,
        "email": context.email,
        "language": _state_locale(ctx.state),
        "stashed_sessions": turn.stashed_sessions,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }

    support_payload = dict(task.payload)
    support_payload["quoted_message_id"] = turn.quoted_message_id
    await enter_task_progress(ctx, task)

    result = cast(
        SupportResult,
        await worker.run(
            payload=support_payload,
            context=context_data,
            user_message=user_msg,
        ),
    )

    read_request = normalize_read_request(task.payload)
    if result.read_result is None and read_request is not None and result.outcome == SupportOutcome.OK:
        result.read_result = ReadResult(
            request=read_request,
            total_count=1 if result.response or result.receipt_jobs else 0,
            returned_count=0 if read_request.response_shape.startswith("fact_") else 1,
        )
    if result.read_result is not None and result.outcome == SupportOutcome.OK:
        viewed_tickets = result.details.get("viewed_support_tickets")
        if isinstance(viewed_tickets, list):
            push_support_ticket_list_frame(
                ctx,
                viewed_tickets,
                read_result=result.read_result,
            )
        else:
            push_read_result_frame(ctx, result.read_result)

    if result.outcome == SupportOutcome.OK:
        complete_task(task)
        if result.handoff is not None and result.handoff.type == "retry_transfer":
            handoff = result.handoff.payload
            transfer_payload = {
                "action": "send_money",
                "amount": handoff.amount,
                "recipient_name": handoff.recipient_name,
                "recipient_account": handoff.recipient_account_number,
                "recipient_bank_code": handoff.recipient_bank_code,
                "bank_name": handoff.recipient_bank_name,
                "narration": handoff.narration,
                "source_bank_name": handoff.source_bank_name,
                "instruction": "Retry the selected transfer",
                "message": turn.last_message_text,
                "skip_extraction": True,
            }
            transfer_payload = {key: value for key, value in transfer_payload.items() if value is not None}
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
            waves.insert(min(next_wave_index(ctx.state), len(waves)), [transfer_task_id])
            ctx.accumulator.set_waves(waves)
            logger.info("support_retry_handoff_materialized", requires_fresh_authorization=True)
        receipt_jobs = [job for job in result.receipt_jobs if isinstance(job, dict)]
        if receipt_jobs:
            publisher = ctx.dependencies.publisher
            if publisher is None:
                ctx.accumulator.say(render_message("query.receipt.failed", _state_locale(ctx.state)))
            else:
                try:
                    for job in receipt_jobs:
                        await publisher.publish("receipt.process", cast(dict[str, Any], job))
                except Exception:
                    ctx.accumulator.say(render_message("query.receipt.failed", _state_locale(ctx.state)))
                else:
                    ctx.accumulator.say(result.response)
        else:
            ctx.accumulator.say(result.response)
    elif result.outcome == SupportOutcome.NEEDS_INPUT:
        set_task_stage(task, TaskStage.EXTRACTED)
        if result.response:
            ctx.accumulator.add_prompt(result.response, task_id)
            ctx.accumulator.add_missing_fields(task_id, ["clarification"])
    elif result.outcome == SupportOutcome.NEEDS_CONFIRMATION:
        set_task_stage(task, TaskStage.AWAITING_CONFIRMATION)
        ctx.accumulator.add_confirmation_task(task_id)
        set_task_confirmation(
            task,
            summary=result.confirmation_summary,
            snapshot=result.confirmation_snapshot,
            update_message=None,
        )
    elif result.outcome == SupportOutcome.FAILED:
        fail_task(
            task,
            result.error
            or render_message(
                "orchestrator.error.support_flow_failed",
                _state_locale(ctx.state),
            ),
        )
        ctx.accumulator.say(render_message("support.unavailable", _state_locale(ctx.state)))

    if result.outcome in {SupportOutcome.NEEDS_INPUT, SupportOutcome.NEEDS_CONFIRMATION}:
        upsert_active_session(
            ctx,
            domain="support",
            state="WAITING_FOR_INPUT",
            interrupt_policy="ALLOW",
            task_id=task_id,
        )
    elif result.outcome in (SupportOutcome.OK, SupportOutcome.FAILED):
        pop_active_session(ctx, domain="support")


__all__ = ["FAQTaskExecutor", "SupportTaskExecutor"]
