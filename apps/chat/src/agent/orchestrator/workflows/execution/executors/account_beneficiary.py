from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import (
    push_account_list_frame,
    push_beneficiary_list_frame,
)
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import _apply_result_patch
from apps.chat.src.agent.orchestrator.workflows.execution.task_input import _maybe_user_message
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    complete_task,
    fail_task,
    set_task_payload_value,
    set_task_stage,
)
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.beneficiaries.formatter import BeneficiaryFormatter
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import AccountOutcome, AccountResult, TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_COUNT_PREVIEW_SHAPES = {"fact_count", "fact_bool"}


def _beneficiary_frame_metadata(task: TaskSpec, viewed: Any) -> dict[str, Any]:
    response_shape = str(task.payload.get("response_shape") or "").strip().lower()
    if response_shape not in _COUNT_PREVIEW_SHAPES or not isinstance(viewed, list):
        return {}
    total_count = len(viewed)
    shown_count = min(3, total_count)
    if total_count <= shown_count:
        return {}
    return {
        "display_shape": "count_preview",
        "response_shape": response_shape,
        "shown_count": shown_count,
        "total_count": total_count,
    }


class AccountTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_account_task(task, task_id, ctx)


class BeneficiaryTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_beneficiary_task(task, task_id, ctx)


async def _execute_account_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
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
    context = loaded_context(ctx.state)
    turn = turn_metadata(ctx.state)
    context_data = {
        "phone_number": turn.phone_number,
        "user_id": context.user_id,
        "profile": context.profile,
        "accounts": context.accounts,
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
        complete_task(task)
        viewed_accounts = result.details.get("viewed_accounts") if isinstance(result.details, dict) else None
        if isinstance(viewed_accounts, list):
            push_account_list_frame(
                ctx,
                [item for item in viewed_accounts if isinstance(item, dict)],
                metadata={
                    "source_domain": "account",
                    "source_action": str(task.payload.get("action") or ""),
                    "response_shape": str(task.payload.get("response_shape") or ""),
                },
            )
        if result.response:
            set_task_payload_value(task, "result", result.response)
        if result.outbox:
            ctx.accumulator.extend_outbox(result.outbox)
        else:
            ctx.accumulator.say(result.response)

    elif result.outcome == AccountOutcome.NEEDS_INPUT:
        set_task_stage(task, TaskStage.EXTRACTED)
        ctx.accumulator.add_missing_fields(task_id, result.required_fields or ["identifier"])
        ctx.accumulator.add_prompt(result.prompt, task_id)

    elif result.outcome == AccountOutcome.FAILED:
        fail_task(
            task,
            result.error
            or render_message(
                "orchestrator.error.account_action_failed",
                _state_locale(ctx.state),
            ),
        )
        ctx.accumulator.say(result.response)


async def _execute_beneficiary_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
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
            set_task_payload_value(task, "intent", action)

        provider = None
        transfer_worker = ctx.services.transfer
        if transfer_worker and hasattr(transfer_worker, "resolver_provider"):
            provider = transfer_worker.resolver_provider

        context = loaded_context(ctx.state)
        turn = turn_metadata(ctx.state)
        context_data = {
            "user_id": context.user_id,
            "phone_number": turn.phone_number,
            "resolver_provider": provider,
            "language": _state_locale(ctx.state),
        }

        result = cast(TransactionResult, await worker.run(payload=task.payload, context=context_data))
        _apply_result_patch(task, result)

        if result.outcome == TransactionOutcome.OK:
            complete_task(task)

            if result.details and "viewed_beneficiaries" in result.details:
                viewed = result.details["viewed_beneficiaries"]
                push_beneficiary_list_frame(ctx, viewed, metadata=_beneficiary_frame_metadata(task, viewed))
                response_shape = str(task.payload.get("response_shape") or "").strip().lower()
                if isinstance(viewed, list) and response_shape in _COUNT_PREVIEW_SHAPES and result.response:
                    body_blocks = BeneficiaryFormatter.format_count_preview_blocks(
                        viewed,
                        result.response.splitlines()[0],
                        locale=_state_locale(ctx.state),
                    )
                elif isinstance(viewed, list):
                    body_blocks = BeneficiaryFormatter.format_beneficiary_list_blocks(
                        viewed,
                        locale=_state_locale(ctx.state),
                    )
                else:
                    body_blocks = None
            else:
                body_blocks = None

            if result.response:
                set_task_payload_value(task, "result", result.response)
                if body_blocks:
                    ctx.accumulator.add_outbox({"type": "say", "text": result.response, "body_blocks": body_blocks})
                else:
                    ctx.accumulator.say(result.response)
        elif result.outcome == TransactionOutcome.FAILED:
            err = result.error or render_message(
                "orchestrator.error.beneficiary_operation_failed",
                _state_locale(ctx.state),
            )
            fail_task(task, err)
            ctx.accumulator.say(err)
        return

    suggestion_service = ctx.dependencies.beneficiary_suggestion_service
    if not suggestion_service:
        logger.error("suggestion_service_missing")
        fail_task(
            task,
            render_message(
                "orchestrator.error.suggestion_service_unavailable",
                _state_locale(ctx.state),
            ),
        )
        return

    alias = task.payload.get("alias")
    turn = turn_metadata(ctx.state)
    try:
        msg = await suggestion_service.save_beneficiary(
            turn.phone_number,
            alias=alias,
            locale=_state_locale(ctx.state),
        )
        complete_task(task)
        set_task_payload_value(task, "result", msg)

        if ctx.current_wave_len == 1:
            ctx.accumulator.say(msg)

    except Exception as exc:
        logger.error("save_beneficiary_exec_error", error=str(exc))
        fail_task(
            task,
            render_message("orchestrator.error.save_beneficiary_failed", _state_locale(ctx.state)),
        )


__all__ = ["AccountTaskExecutor", "BeneficiaryTaskExecutor"]
