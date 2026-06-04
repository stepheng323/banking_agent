from typing import cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.task_handlers.context_frames import (
    push_account_list_frame,
    push_beneficiary_list_frame,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.runtime import (
    _apply_result_patch,
    _get_worker,
    _maybe_user_message,
    _state_locale,
)
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import AccountOutcome, AccountResult, TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_account_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    worker = _get_worker(
        ctx.services,
        "account",
        task,
        log_key="account_worker_missing",
        error_message=render_message("orchestrator.error.account_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    if not user_msg:
        message_from_payload = task.payload.get("message") or task.payload.get("instruction")
        user_msg = message_from_payload if isinstance(message_from_payload, str) else None
    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "profile": ctx.state.loaded_context.get("profile", {}),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "language": _state_locale(ctx.state),
    }

    result = cast(
        AccountResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
        ),
    )

    _apply_result_patch(task, result)

    if result.outcome == AccountOutcome.OK:
        task.stage = TaskStage.COMPLETED
        viewed_accounts = result.details.get("viewed_accounts") if isinstance(result.details, dict) else None
        if isinstance(viewed_accounts, list):
            push_account_list_frame(ctx, [item for item in viewed_accounts if isinstance(item, dict)])
        if result.response:
            task.payload["result"] = result.response
            ctx.accumulator.say(result.response)
        ctx.accumulator.extend_outbox(result.outbox)

    elif result.outcome == AccountOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        ctx.accumulator.add_missing_fields(task_id, result.required_fields or ["identifier"])
        ctx.accumulator.add_prompt(result.prompt, task_id)

    elif result.outcome == AccountOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or render_message(
            "orchestrator.error.account_action_failed",
            _state_locale(ctx.state),
        )
        ctx.accumulator.say(result.response)


async def handle_beneficiary_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    del task_id
    action = task.payload.get("action")
    is_management = (
        task.payload.get("intent")
        or task.payload.get("list_intent")
        or action in ("list_beneficiaries", "add_beneficiary", "delete_beneficiary", "update_beneficiary")
    )

    if is_management:
        worker = _get_worker(
            ctx.services,
            "beneficiary",
            task,
            log_key="beneficiary_worker_missing",
            error_message=render_message(
                "orchestrator.error.beneficiary_operation_failed",
                _state_locale(ctx.state),
            ),
        )
        if not worker:
            return

        if not task.payload.get("intent") and action:
            task.payload["intent"] = action

        provider = None
        transfer_worker = ctx.services.transfer
        if transfer_worker and hasattr(transfer_worker, "resolver_provider"):
            provider = transfer_worker.resolver_provider

        context_data = {
            "user_id": ctx.state.loaded_context.get("user_id"),
            "phone_number": ctx.state.phone_number,
            "resolver_provider": provider,
            "language": _state_locale(ctx.state),
        }

        result = cast(TransactionResult, await worker.run(payload=task.payload, context=context_data))
        _apply_result_patch(task, result)

        if result.outcome == TransactionOutcome.OK:
            task.stage = TaskStage.COMPLETED

            if result.details and "viewed_beneficiaries" in result.details:
                viewed = result.details["viewed_beneficiaries"]
                push_beneficiary_list_frame(ctx, viewed)

            if result.response:
                task.payload["result"] = result.response
                ctx.accumulator.say(result.response)
        elif result.outcome == TransactionOutcome.FAILED:
            task.stage = TaskStage.FAILED
            err = result.error or render_message(
                "orchestrator.error.beneficiary_operation_failed",
                _state_locale(ctx.state),
            )
            task.payload["error"] = err
            ctx.accumulator.say(err)
        return

    suggestion_service = ctx.dependencies.beneficiary_suggestion_service
    if not suggestion_service:
        logger.error("suggestion_service_missing")
        task.stage = TaskStage.FAILED
        task.payload["error"] = render_message(
            "orchestrator.error.suggestion_service_unavailable",
            _state_locale(ctx.state),
        )
        return

    alias = task.payload.get("alias")
    try:
        msg = await suggestion_service.save_beneficiary(
            ctx.state.phone_number,
            alias=alias,
            locale=_state_locale(ctx.state),
        )
        task.stage = TaskStage.COMPLETED
        task.payload["result"] = msg

        if ctx.current_wave_len == 1:
            ctx.accumulator.say(msg)

    except Exception as exc:
        logger.error("save_beneficiary_exec_error", error=str(exc))
        task.stage = TaskStage.FAILED
        task.payload["error"] = render_message("orchestrator.error.save_beneficiary_failed", _state_locale(ctx.state))
