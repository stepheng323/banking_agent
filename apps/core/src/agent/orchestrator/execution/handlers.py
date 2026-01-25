from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    FAQOutcome,
    SupportOutcome,
    TaskStage,
    TransactionOutcome,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionAggregation:
    def __init__(self, tasks: dict[str, Any]) -> None:
        self.updates: dict[str, Any] = {"tasks": tasks}
        self.missing_fields_by_task: dict[str, list[str]] = {}
        self.needs_confirm_tasks: list[str] = []
        self.needs_auth_tasks: list[str] = []
        self.prompts: list[str] = []

    def add_outbox(self, entry: dict[str, Any]) -> None:
        self.updates.setdefault("outbox", [])
        self.updates["outbox"].append(entry)

    def extend_outbox(self, entries: list[dict[str, Any]] | None) -> None:
        if entries:
            self.updates.setdefault("outbox", [])
            self.updates["outbox"].extend(entries)

    def say(self, text: str | None) -> None:
        if text:
            self.add_outbox({"type": "say", "text": text})

    def add_prompt(self, prompt: str | None) -> None:
        if prompt:
            self.prompts.append(prompt)

    def add_missing_fields(self, task_id: str, fields: list[str] | None) -> None:
        if fields:
            self.missing_fields_by_task[task_id] = fields


@dataclass
class ExecutionContext:
    state: OrchestratorState
    config: RunnableConfig
    services: dict[str, Any]
    current_wave_len: int
    agg: ExecutionAggregation


def _maybe_user_message(task: Any, state: OrchestratorState) -> str | None:
    if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
        return state.last_message_text
    return None


def _get_worker(
    services: dict[str, Any],
    name: str,
    task: Any,
    *,
    log_key: str,
    error_message: str,
) -> Any | None:
    worker = services.get(name)
    if not worker:
        logger.error(log_key)
        task.stage = TaskStage.FAILED
        task.payload["error"] = error_message
        return None
    return worker


def _apply_result_patch(task: Any, result: Any) -> None:
    if result.patch:
        task.payload.update(result.patch)


def _set_confirmation(task: Any, result: Any, *, gate_on: str) -> None:
    if gate_on == "summary" and not getattr(result, "confirmation_summary", None):
        return
    if gate_on == "snapshot" and not getattr(result, "confirmation_snapshot", None):
        return

    confirmation = task.payload.setdefault("confirmation", {})
    confirmation["summary"] = getattr(result, "confirmation_summary", None)
    confirmation["snapshot"] = getattr(result, "confirmation_snapshot", None)
    update_message = getattr(result, "update_message", None)
    if update_message:
        confirmation["update_message"] = update_message


def _handle_transaction_outcome(
    task: Any,
    task_id: str,
    result: Any,
    agg: ExecutionAggregation,
    *,
    confirmation_gate: str,
    default_error: str | None,
) -> None:
    if result.outcome == TransactionOutcome.OK:
        if result.receipt:
            task.stage = TaskStage.COMPLETED
            task.payload["receipt"] = result.receipt

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        agg.add_missing_fields(task_id, result.required_fields)
        agg.add_prompt(result.prompt)

    elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
        task.stage = TaskStage.AWAITING_CONFIRMATION
        agg.needs_confirm_tasks.append(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.NEEDS_AUTH:
        task.stage = TaskStage.AWAITING_AUTH
        agg.needs_auth_tasks.append(task_id)

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        if default_error is None:
            task.payload["error"] = result.error
        else:
            task.payload["error"] = result.error or default_error


async def handle_transfer_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "transfer",
        task,
        log_key="transfer_worker_missing",
        error_message="System error: Transfer worker unavailable",
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
    }

    logger.info("transfer_worker_start", payload=task.payload)
    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
        pin_verified=ctx.state.pin_verified,
    )
    logger.info(
        "transfer_worker_returned",
        outcome=result.outcome,
        has_receipt=bool(result.receipt),
        result_obj=str(result),
    )

    _apply_result_patch(task, result)

    if result.outcome == TransactionOutcome.OK and result.receipt:
        logger.info("transfer_worker_ok_branch", has_receipt=bool(result.receipt))

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.agg,
        confirmation_gate="snapshot",
        default_error=None,
    )


async def handle_account_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "account",
        task,
        log_key="account_worker_missing",
        error_message="System error: Account worker unavailable",
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "profile": ctx.state.loaded_context.get("profile", {}),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "language": ctx.state.loaded_context.get("language"),
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
    )

    _apply_result_patch(task, result)

    if result.outcome == AccountOutcome.OK:
        task.stage = TaskStage.COMPLETED
        if result.response:
            task.payload["result"] = result.response
            ctx.agg.say(result.response)
        ctx.agg.extend_outbox(result.outbox)

    elif result.outcome == AccountOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        ctx.agg.add_missing_fields(task_id, result.required_fields or ["identifier"])
        ctx.agg.add_prompt(result.prompt)

    elif result.outcome == AccountOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or "Account action failed."
        ctx.agg.say(result.response)


async def handle_beneficiary_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    # Check for explicit management intent (List/Add/Delete)
    if task.payload.get("intent") or task.payload.get("list_intent"):
        from apps.core.src.agent.graphs.beneficiary.worker import BeneficiaryWorker
        from apps.core.src.agent.orchestrator.execution.handlers import _apply_result_patch

        worker = BeneficiaryWorker()

        # Try to get banking provider for validation
        provider = None
        transfer_worker = ctx.services.get("transfer")
        if transfer_worker and hasattr(transfer_worker, "banking_provider"):
            provider = transfer_worker.banking_provider

        context_data = {
            "user_id": ctx.state.loaded_context.get("user_id"),
            "phone_number": ctx.state.phone_number,
            "banking_provider": provider,
        }

        result = await worker.run(task.payload, context_data)
        _apply_result_patch(task, result)

        if result.outcome == TransactionOutcome.OK:
            task.stage = TaskStage.COMPLETED
            if result.response:
                task.payload["result"] = result.response
                ctx.agg.say(result.response)
        elif result.outcome == TransactionOutcome.FAILED:
            task.stage = TaskStage.FAILED
            err = result.error or "Beneficiary operation failed."
            task.payload["error"] = err
            ctx.agg.say(err)
        return

    # Fallback to suggestion service (Reactive Save)
    suggestion_service = ctx.config["configurable"].get("beneficiary_suggestion_service")
    if not suggestion_service:
        logger.error("suggestion_service_missing")
        task.stage = TaskStage.FAILED
        task.payload["error"] = "System error: Suggestion service unavailable"
        return

    alias = task.payload.get("alias")
    try:
        msg = await suggestion_service.save_beneficiary(ctx.state.phone_number, alias=alias)
        task.stage = TaskStage.COMPLETED
        task.payload["result"] = msg

        if ctx.current_wave_len == 1:
            ctx.agg.say(msg)

    except Exception as exc:
        logger.error("save_beneficiary_exec_error", error=str(exc))
        task.stage = TaskStage.FAILED
        task.payload["error"] = "Failed to save beneficiary."


async def handle_airtime_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "airtime",
        task,
        log_key="airtime_worker_missing",
        error_message="System error: Airtime worker unavailable",
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    context_data = {
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
        pin_verified=ctx.state.pin_verified,
    )

    _apply_result_patch(task, result)

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.agg,
        confirmation_gate="summary",
        default_error="Airtime purchase failed",
    )


async def handle_query_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "query",
        task,
        log_key="query_worker_missing",
        error_message="System error: Query worker unavailable",
    )
    if not worker:
        return

    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "profile": ctx.state.loaded_context.get("profile", {}),
        "accounts": ctx.state.loaded_context.get("accounts", []),
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
    )

    _apply_result_patch(task, result)

    if result.outcome == TransactionOutcome.OK:
        task.stage = TaskStage.COMPLETED
        if result.response:
            task.payload["result"] = result.response
            ctx.agg.say(result.response)

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        if result.response:
            ctx.agg.add_prompt(result.response)
            ctx.agg.add_missing_fields(task_id, ["clarification"])

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or "Query processing failed."
        ctx.agg.say(result.response)


async def handle_data_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "data",
        task,
        log_key="data_worker_missing",
        error_message="System error: Data worker unavailable",
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
        pin_verified=ctx.state.pin_verified,
    )

    _apply_result_patch(task, result)

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.agg,
        confirmation_gate="summary",
        default_error="Data purchase failed",
    )


async def handle_faq_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "faq",
        task,
        log_key="faq_worker_missing",
        error_message="System error: FAQ worker unavailable",
    )
    if not worker:
        return

    user_msg = ctx.state.last_message_text
    context_data = {"phone_number": ctx.state.phone_number}

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
    )

    if result.outcome == FAQOutcome.OK:
        task.stage = TaskStage.COMPLETED
        ctx.agg.say(result.response)
    elif result.outcome == FAQOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or "FAQ failed"
        ctx.agg.say("I'm having trouble retrieving that information.")


async def handle_support_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    worker = _get_worker(
        ctx.services,
        "support",
        task,
        log_key="support_worker_missing",
        error_message="System error: Support worker unavailable",
    )
    if not worker:
        return

    user_msg = ctx.state.last_message_text
    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "email": ctx.state.loaded_context.get("profile", {}).get("email"),
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
    )

    if result.outcome == SupportOutcome.OK:
        task.stage = TaskStage.COMPLETED
        ctx.agg.say(result.response)
    elif result.outcome == SupportOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        if result.response:
            ctx.agg.add_prompt(result.response)
            ctx.agg.add_missing_fields(task_id, ["clarification"])
    elif result.outcome == SupportOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or "Support flow failed"
        ctx.agg.say("I can't access support right now.")
