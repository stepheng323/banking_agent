from typing import cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import (
    invalidate_conversation_set_frames,
    push_account_list_frame,
    push_beneficiary_list_frame,
    push_read_result_frame,
)
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.progress import enter_task_progress
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import (
    _apply_result_patch,
    _handle_transaction_outcome,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_input import _maybe_user_message
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    complete_task,
    fail_task,
    set_task_confirmation,
    set_task_payload_value,
    set_task_stage,
)
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.beneficiaries.formatter import BeneficiaryFormatter
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import AccountOutcome, AccountResult, TransactionOutcome, TransactionResult
from shared.types.balance import BalanceConversationState, BalanceQueryContract
from shared.types.read import ReadRequest, ReadResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
        "stashed_sessions": turn.stashed_sessions,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }
    await enter_task_progress(ctx, task)

    try:
        result = cast(
            AccountResult,
            await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
            ),
        )
    except Exception as exc:
        logger.error(
            "account_worker_execution_exception",
            task_id=task_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        result = AccountResult(
            outcome=AccountOutcome.FAILED,
            error=render_message("orchestrator.error.account_action_failed", _state_locale(ctx.state)),
        )

    action = str(task.payload.get("action") or "").strip()
    if (
        result.outcome == AccountOutcome.OK
        and result.read_result is None
        and action
        in {
            "check_balance",
        }
    ):
        viewed = result.details.get("viewed_accounts") if isinstance(result.details, dict) else None
        derived_viewed_accounts = (
            [item for item in viewed if isinstance(item, dict)] if isinstance(viewed, list) else []
        )
        bank_name = str(task.payload.get("identifier") or "").strip() or None
        if bank_name is None and len(derived_viewed_accounts) == 1:
            bank_name = str(derived_viewed_accounts[0].get("bank_name") or "").strip() or None
        request = ReadRequest(subject="balance", response_shape="fact_value", bank_name=bank_name)
        result.read_result = ReadResult(
            request=request,
            total_count=len(derived_viewed_accounts),
            returned_count=0,
        )
        logger.info(
            "account_typed_action_read_contract_derived",
            subject="balance",
            response_shape="fact_value",
            bank_filter_present=bank_name is not None,
        )

    _apply_result_patch(task, result)
    invalidated_domain = (
        result.patch.get("invalidate_conversation_set_domain") if isinstance(result.patch, dict) else None
    )
    if result.outcome == AccountOutcome.OK and isinstance(invalidated_domain, str):
        invalidate_conversation_set_frames(ctx, invalidated_domain)

    if result.outcome == AccountOutcome.OK:
        complete_task(task)
        viewed_accounts = result.details.get("viewed_accounts") if isinstance(result.details, dict) else None
        if isinstance(viewed_accounts, list):
            metadata: dict[str, object] = {
                "source_domain": "account",
                "source_action": str(task.payload.get("action") or ""),
            }
            if result.read_result is not None:
                metadata.update(
                    {
                        "read_request": result.read_result.request.model_dump(mode="json", exclude_none=True),
                        "total_count": result.read_result.total_count,
                        "has_next": result.read_result.has_next,
                        "has_previous": result.read_result.has_previous,
                    }
                )
            raw_balance_contract = task.payload.get("balance_contract")
            if isinstance(raw_balance_contract, dict):
                try:
                    balance_contract = BalanceQueryContract.model_validate(raw_balance_contract)
                except ValueError:
                    balance_contract = None
                if balance_contract is not None:
                    metadata["balance_contract"] = balance_contract.model_dump(mode="json", exclude_none=True)
                    raw_balance_state = task.payload.get("balance_conversation_state")
                    try:
                        balance_state = (
                            BalanceConversationState.model_validate(raw_balance_state)
                            if isinstance(raw_balance_state, dict)
                            else BalanceConversationState(last_operation=balance_contract.operation)
                        )
                    except ValueError:
                        balance_state = BalanceConversationState(last_operation=balance_contract.operation)
                    result_banks = [
                        str(item.get("bank_name") or "").strip()
                        for item in viewed_accounts
                        if isinstance(item, dict) and str(item.get("bank_name") or "").strip()
                    ]
                    mentioned = list(balance_state.mentioned_banks)
                    seen = {name.casefold() for name in mentioned}
                    for bank_name in result_banks:
                        if bank_name.casefold() not in seen:
                            mentioned.append(bank_name)
                            seen.add(bank_name.casefold())
                    balance_state = balance_state.model_copy(
                        update={
                            "focused_bank": result_banks[0] if len(result_banks) == 1 else balance_state.focused_bank,
                            "mentioned_banks": mentioned,
                            "last_result_banks": result_banks,
                            "last_operation": balance_contract.operation,
                        }
                    )
                    metadata["balance_conversation_state"] = balance_state.model_dump(mode="json", exclude_none=True)
            raw_lifecycle_contract = task.payload.get("account_lifecycle_contract")
            if isinstance(raw_lifecycle_contract, dict):
                metadata["account_lifecycle_contract"] = raw_lifecycle_contract
            raw_set_state = task.payload.get("conversation_set_state")
            if isinstance(raw_set_state, dict):
                metadata["conversation_set_state"] = raw_set_state
            push_account_list_frame(
                ctx,
                [item for item in viewed_accounts if isinstance(item, dict)],
                metadata=metadata,
            )
        elif result.read_result is not None:
            raw_lifecycle_contract = task.payload.get("account_lifecycle_contract")
            push_read_result_frame(
                ctx,
                result.read_result,
                metadata=(
                    {"account_lifecycle_contract": raw_lifecycle_contract}
                    if isinstance(raw_lifecycle_contract, dict)
                    else None
                ),
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

    elif result.outcome == AccountOutcome.NEEDS_CONFIRMATION:
        set_task_stage(task, TaskStage.AWAITING_CONFIRMATION)
        ctx.accumulator.add_confirmation_task(task_id)
        set_task_confirmation(
            task,
            summary=result.confirmation_summary,
            snapshot=result.confirmation_snapshot,
            update_message=result.update_message,
        )

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
    action = task.payload.get("action")
    is_management = (
        task.payload.get("intent")
        or task.payload.get("list_intent")
        or action in ("list_beneficiaries", "delete_beneficiary", "rename_beneficiary")
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
            "stashed_sessions": turn.stashed_sessions,
            "progress_tracker": ctx.dependencies.progress_tracker,
        }
        await enter_task_progress(ctx, task)

        result = cast(TransactionResult, await worker.run(payload=task.payload, context=context_data))
        _apply_result_patch(task, result)
        invalidated_domain = (
            result.patch.get("invalidate_conversation_set_domain") if isinstance(result.patch, dict) else None
        )
        if result.outcome == TransactionOutcome.OK and isinstance(invalidated_domain, str):
            invalidate_conversation_set_frames(ctx, invalidated_domain)

        if result.outcome == TransactionOutcome.OK:
            complete_task(task)

            if result.details and "viewed_beneficiaries" in result.details:
                viewed = result.details["viewed_beneficiaries"]
                read_result = result.read_result
                metadata = (
                    {
                        "read_request": read_result.request.model_dump(mode="json", exclude_none=True),
                        "total_count": read_result.total_count,
                        "has_next": read_result.has_next,
                        "has_previous": read_result.has_previous,
                    }
                    if read_result is not None
                    else {}
                )
                raw_contract = task.payload.get("beneficiary_contract")
                if isinstance(raw_contract, dict):
                    metadata["beneficiary_contract"] = raw_contract
                raw_set_state = task.payload.get("conversation_set_state")
                if isinstance(raw_set_state, dict):
                    metadata["conversation_set_state"] = raw_set_state
                push_beneficiary_list_frame(ctx, viewed, metadata=metadata)
                if isinstance(viewed, list):
                    body_blocks = BeneficiaryFormatter.format_beneficiary_list_blocks(
                        viewed,
                        locale=_state_locale(ctx.state),
                        name_filter=(read_result.request.entity_name if read_result is not None else None),
                        has_next=(read_result.has_next if read_result is not None else False),
                    )
                else:
                    body_blocks = None
            else:
                body_blocks = None
                if result.read_result is not None:
                    raw_contract = task.payload.get("beneficiary_contract")
                    push_read_result_frame(
                        ctx,
                        result.read_result,
                        metadata=({"beneficiary_contract": raw_contract} if isinstance(raw_contract, dict) else None),
                    )

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
        else:
            _handle_transaction_outcome(
                task,
                task_id,
                result,
                ctx.accumulator,
                confirmation_gate="snapshot",
                default_error=render_message(
                    "orchestrator.error.beneficiary_operation_failed",
                    _state_locale(ctx.state),
                ),
            )
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
