from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import TransferTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    complete_task,
    fail_task,
    replace_task_payload,
    set_task_payload_value,
    set_task_stage,
    set_task_type,
)
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.intent.routing_signals import looks_like_transaction_replay_modifier_request
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import FAQOutcome, FAQResult, SupportOutcome, SupportResult
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

    user_msg = ctx.state.last_message_text
    context_data = {
        "phone_number": ctx.state.phone_number,
        "language": _state_locale(ctx.state),
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
    user_msg = ctx.state.last_message_text
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

    context_data = {
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "channel_identity": ctx.state.channel_identity,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "email": ctx.state.loaded_context.get("profile", {}).get("email"),
        "language": _state_locale(ctx.state),
    }

    support_payload = dict(task.payload)
    support_payload["quoted_message_id"] = ctx.state.quoted_message_id

    result = cast(
        SupportResult,
        await worker.run(
            payload=support_payload,
            context=context_data,
            user_message=user_msg,
        ),
    )

    if result.outcome == SupportOutcome.OK:
        complete_task(task)
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

    stack = list(ctx.state.session_stack)
    if result.outcome == SupportOutcome.NEEDS_INPUT:
        if stack and stack[-1].domain == "support":
            stack[-1].state = "WAITING_FOR_INPUT"
        else:
            stack.append(
                ActiveSession(
                    domain="support",
                    state="WAITING_FOR_INPUT",
                    interrupt_policy="ALLOW",
                    resume_hint={"task_id": task_id},
                )
            )
        ctx.accumulator.set_session_stack(stack)
    elif result.outcome in (SupportOutcome.OK, SupportOutcome.FAILED):
        if stack and stack[-1].domain == "support":
            stack.pop()
            ctx.accumulator.set_session_stack(stack)


__all__ = ["FAQTaskExecutor", "SupportTaskExecutor"]
