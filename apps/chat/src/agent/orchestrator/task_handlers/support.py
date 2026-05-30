from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, FAQOutcome, SupportOutcome, TaskStage
from apps.chat.src.agent.orchestrator.task_handlers.runtime import ExecutionContext, _get_worker, _state_locale
from apps.chat.src.agent.orchestrator.task_handlers.transfer import handle_transfer_task
from apps.chat.src.agent.shared.routing_signals import looks_like_transaction_replay_modifier_request
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_faq_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
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

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
    )

    if result.outcome == FAQOutcome.OK:
        if result.should_route_to_support:
            logger.info("faq_task_delegating_to_support", task_id=task_id)
            task.type = "support"
            task.payload["action"] = "handle_request"
            await handle_support_task(task, task_id, ctx)
            return

        task.stage = TaskStage.COMPLETED
        ctx.agg.say(result.response)
    elif result.outcome == FAQOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or render_message(
            "orchestrator.error.faq_failed",
            _state_locale(ctx.state),
        )
        ctx.agg.say(render_message("faq.info_trouble", _state_locale(ctx.state)))


async def handle_support_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    user_msg = ctx.state.last_message_text
    if looks_like_transaction_replay_modifier_request(user_msg):
        logger.info("support_task_replay_modifier_rerouted_to_transfer", task_id=task_id)
        task.type = "transfer"
        task.payload.clear()
        task.payload.update(
            {
                "message": user_msg,
                "instruction": user_msg,
            }
        )
        await handle_transfer_task(task, task_id, ctx)
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

    result = await worker.run(
        payload=support_payload,
        context=context_data,
        user_message=user_msg,
    )

    if result.outcome == SupportOutcome.OK:
        task.stage = TaskStage.COMPLETED
        receipt_jobs = [job for job in result.receipt_jobs if isinstance(job, dict)]
        if receipt_jobs:
            publisher = ctx.config.get("configurable", {}).get("publisher")
            if publisher is None:
                ctx.agg.say(render_message("query.receipt.failed", _state_locale(ctx.state)))
            else:
                try:
                    for job in receipt_jobs:
                        await publisher.publish("receipt.process", job)
                except Exception:
                    ctx.agg.say(render_message("query.receipt.failed", _state_locale(ctx.state)))
                else:
                    ctx.agg.say(result.response)
        else:
            ctx.agg.say(result.response)
    elif result.outcome == SupportOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        if result.response:
            ctx.agg.add_prompt(result.response, task_id)
            ctx.agg.add_missing_fields(task_id, ["clarification"])
    elif result.outcome == SupportOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or render_message(
            "orchestrator.error.support_flow_failed",
            _state_locale(ctx.state),
        )
        ctx.agg.say(render_message("support.unavailable", _state_locale(ctx.state)))

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
        ctx.agg.updates["session_stack"] = stack
    elif result.outcome in (SupportOutcome.OK, SupportOutcome.FAILED):
        if stack and stack[-1].domain == "support":
            stack.pop()
            ctx.agg.updates["session_stack"] = stack
