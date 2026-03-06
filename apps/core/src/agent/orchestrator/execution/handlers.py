import time
from dataclasses import dataclass
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    ActiveSession,
    FAQOutcome,
    SupportOutcome,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger
from shared.utils.serialization import sqlalchemy_to_dict

logger = get_logger(__name__)

SessionState = Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH", "RUNNING"]


class ExecutionAggregation:
    def __init__(self, tasks: dict[str, Any]) -> None:
        self.updates: dict[str, Any] = {"tasks": tasks}
        self.missing_fields_by_task: dict[str, list[str]] = {}
        self.details_by_task: dict[str, dict[str, Any]] = {}
        self.needs_confirm_tasks: list[str] = []
        self.needs_auth_tasks: list[str] = []
        self.prompts: list[str] = []
        self.prompts_by_task: dict[str, str] = {}
        self.feedback_messages: list[str] = []
        self.source_bank_hints: list[str] = []

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

    def add_prompt(self, prompt: str | None, task_id: str | None = None) -> None:
        if prompt:
            self.prompts.append(prompt)
            if task_id:
                self.prompts_by_task[task_id] = prompt

    def add_missing_fields(self, task_id: str, fields: list[str] | None) -> None:
        if fields:
            self.missing_fields_by_task[task_id] = fields

    def add_details(self, task_id: str, details: dict[str, Any] | None) -> None:
        if details:
            self.details_by_task[task_id] = details


@dataclass
class ExecutionContext:
    state: OrchestratorState
    config: RunnableConfig
    services: dict[str, Any]
    current_wave_len: int
    agg: ExecutionAggregation


def _state_locale(state: OrchestratorState) -> str:
    return cast(str, LocaleManager.normalize(state.loaded_context.get("language")).value)


def _normalize_beneficiary_rows(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
        else:
            normalized.append(sqlalchemy_to_dict(row))
    return normalized


def _is_resume_prompt_frame(frame: ContextFrame) -> bool:
    """Return True if a context frame represents a resume prompt."""
    return any(item.data.get("resume_prompt") is True for item in frame.items)


def _clear_resume_prompt_frames(frames: list[ContextFrame]) -> list[ContextFrame]:
    """Remove stale resume prompt frames after accept/decline."""
    return [frame for frame in frames if not _is_resume_prompt_frame(frame)]


def _has_recent_beneficiary_context(frames: list[ContextFrame]) -> bool:
    """True when a non-expired beneficiary-list frame exists with entries."""
    now = int(time.time())
    for frame in reversed(frames):
        if frame.frame_type != ContextFrameType.BENEFICIARY_LIST:
            continue
        if (frame.created_at_ts + frame.ttl_seconds) <= now:
            continue
        return bool(frame.items)
    return False


def _maybe_user_message(task: Any, state: OrchestratorState) -> str | None:
    logger.info(
        "maybe_user_msg_check",
        task_id=task.id,
        last_int=state.last_interrupt.task_ids if state.last_interrupt else None,
    )
    if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
        # [Prevention of Cross-Contamination]
        # Only provide user input to tasks that were actively soliciting it (part of the last interrupt).
        # This prevents background/suppressed tasks from consuming input meant for the active task.
        if state.last_interrupt and task.id not in state.last_interrupt.task_ids:
            return None
        return cast(str | None, state.last_message_text)
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


def _next_query_handoff_transfer_task_id(tasks: dict[str, Any]) -> str:
    index = 1
    candidate = f"query_handoff_transfer_{index}"
    while candidate in tasks:
        index += 1
        candidate = f"query_handoff_transfer_{index}"
    return candidate


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
        task.stage = TaskStage.COMPLETED
        if result.receipt:
            task.payload["receipt"] = result.receipt

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        agg.add_missing_fields(task_id, result.required_fields)
        agg.add_details(task_id, result.details)
        agg.add_prompt(result.prompt, task_id)
        if result.update_message:
            agg.feedback_messages.append(result.update_message)

        # Capture source bank hint if present in patch (worker returns what it tried to match)
        if hint := result.patch.get("source_bank_name"):
            agg.source_bank_hints.append(hint)

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
        error_message=render_message("orchestrator.error.transfer_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    required_fields: list[str] = []
    previous_response: str | None = None
    if ctx.state.last_interrupt and task_id in ctx.state.last_interrupt.task_ids:
        raw_required_fields = ctx.state.last_interrupt.fields_by_task.get(task_id, [])
        required_fields = [field for field in raw_required_fields if isinstance(field, str)]
        previous_response = ctx.state.last_interrupt.prompt

    user_msg = _maybe_user_message(task, ctx.state)

    awaiting_raw_slot_input = bool(required_fields)
    needs_account_selection = not task.payload.get("source_account_id")
    if task.payload.get("recipient_name") and not needs_account_selection and not awaiting_raw_slot_input:
        r_name = task.payload["recipient_name"]
        if isinstance(r_name, str) and r_name.lower() not in ("him", "her", "them", "that", "it", "this", "previous"):
            amt = task.payload.get("amount") or ""
            user_msg = f"Send {amt} to {r_name}"
            logger.info("user_msg_synthesized", msg=user_msg)

    beneficiaries = ctx.state.loaded_context.get("beneficiaries", [])
    if not isinstance(beneficiaries, list):
        beneficiaries = []

    recipient_name = task.payload.get("recipient_name")
    beneficiary_repo = ctx.config["configurable"].get("beneficiary_repo")
    user_id = ctx.state.loaded_context.get("user_id")
    if (
        isinstance(recipient_name, str)
        and recipient_name.strip()
        and not beneficiaries
        and beneficiary_repo
        and user_id
    ):
        try:
            fetched_rows = await beneficiary_repo.get_by_user(str(user_id), beneficiary_type="transfer")
            beneficiaries = _normalize_beneficiary_rows(fetched_rows if isinstance(fetched_rows, list) else [])
            if isinstance(ctx.state.loaded_context, dict):
                ctx.state.loaded_context["beneficiaries"] = beneficiaries
            logger.info(
                "transfer_beneficiaries_reloaded_for_resolution",
                user_id=str(user_id),
                fetched_count=len(beneficiaries),
            )
        except Exception as e:
            logger.warning(
                "transfer_beneficiary_reload_failed",
                user_id=str(user_id),
                error=str(e),
            )

    context_data = {
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "channel_identity": ctx.state.channel_identity,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": beneficiaries,
        "recent_beneficiary_context": _has_recent_beneficiary_context(ctx.state.context_frames),
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
    }

    logger.info("transfer_worker_start", payload=task.payload, task_id=task_id)
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
        task_id=task_id,
        patch_skip_ext=result.patch.get("skip_extraction") if result.patch else None,
    )

    _apply_result_patch(task, result)
    if result.response:
        ctx.agg.say(result.response)

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

    stack = list(ctx.state.session_stack)
    if result.outcome in (
        TransactionOutcome.NEEDS_INPUT,
        TransactionOutcome.NEEDS_AUTH,
        TransactionOutcome.NEEDS_CONFIRMATION,
    ):
        state_map: dict[TransactionOutcome, SessionState] = {
            TransactionOutcome.NEEDS_INPUT: "WAITING_FOR_INPUT",
            TransactionOutcome.NEEDS_AUTH: "WAITING_FOR_AUTH",
            TransactionOutcome.NEEDS_CONFIRMATION: "WAITING_FOR_INPUT",
        }
        current_state: SessionState = state_map[result.outcome]

        if stack and stack[-1].domain == "transfer":
            stack[-1].state = current_state
        else:
            stack.append(
                ActiveSession(
                    domain="transfer",
                    state=current_state,
                    interrupt_policy="BLOCK" if result.outcome == TransactionOutcome.NEEDS_AUTH else "CONFIRM",
                    resume_hint={"task_id": task_id},
                )
            )
        ctx.agg.updates["session_stack"] = stack

    elif result.outcome in (TransactionOutcome.OK, TransactionOutcome.FAILED) and result.is_terminal:
        if stack and stack[-1].domain == "transfer":
            stack.pop()
            ctx.agg.updates["session_stack"] = stack


async def handle_account_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
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
        ctx.agg.add_prompt(result.prompt, task_id)

    elif result.outcome == AccountOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or render_message(
            "orchestrator.error.account_action_failed",
            _state_locale(ctx.state),
        )
        ctx.agg.say(result.response)


async def handle_beneficiary_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    action = task.payload.get("action")
    is_management = (
        task.payload.get("intent")
        or task.payload.get("list_intent")
        or action in ("list_beneficiaries", "add_beneficiary", "delete_beneficiary", "update_beneficiary")
    )

    if is_management:
        from apps.core.src.agent.graphs.beneficiary.worker import BeneficiaryWorker
        from apps.core.src.agent.orchestrator.execution.handlers import _apply_result_patch

        if not task.payload.get("intent") and action:
            task.payload["intent"] = action

        worker = BeneficiaryWorker()

        provider = None
        transfer_worker = ctx.services.get("transfer")
        if transfer_worker and hasattr(transfer_worker, "resolver_provider"):
            provider = transfer_worker.resolver_provider

        context_data = {
            "user_id": ctx.state.loaded_context.get("user_id"),
            "phone_number": ctx.state.phone_number,
            "resolver_provider": provider,
            "language": _state_locale(ctx.state),
        }

        result = await worker.run(task.payload, context_data)
        _apply_result_patch(task, result)

        if result.outcome == TransactionOutcome.OK:
            task.stage = TaskStage.COMPLETED

            # [NEW] Context Push (Pattern A)
            if result.details and "viewed_beneficiaries" in result.details:
                viewed = result.details["viewed_beneficiaries"]
                if viewed:
                    import time

                    ctx_manager = OrchestratorContextManager()

                    entities = []
                    for b in viewed:
                        entities.append(
                            ContextEntity(
                                entity_type=EntityType.BENEFICIARY, label=b.get("alias") or b.get("name"), data=b
                            )
                        )

                    frame = ContextFrame(
                        frame_id=f"frame_{int(time.time())}",
                        frame_type=ContextFrameType.BENEFICIARY_LIST,
                        items=entities,
                        created_at_ts=int(time.time()),
                        source_message_id=ctx.state.last_message_id,
                    )

                    # Update State via Manager
                    ctx_manager.push_frame(ctx.state, frame)

                    ctx.agg.updates["context_frames"] = ctx.state.context_frames

                    logger.info("context_frame_pushed", type="beneficiary_list", count=len(entities))

            if result.response:
                task.payload["result"] = result.response
                ctx.agg.say(result.response)
        elif result.outcome == TransactionOutcome.FAILED:
            task.stage = TaskStage.FAILED
            err = result.error or render_message(
                "orchestrator.error.beneficiary_operation_failed",
                _state_locale(ctx.state),
            )
            task.payload["error"] = err
            ctx.agg.say(err)
        return

    # Fallback to suggestion service (Reactive Save)
    suggestion_service = ctx.config["configurable"].get("beneficiary_suggestion_service")
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
            ctx.agg.say(msg)

    except Exception as exc:
        logger.error("save_beneficiary_exec_error", error=str(exc))
        task.stage = TaskStage.FAILED
        task.payload["error"] = render_message("orchestrator.error.save_beneficiary_failed", _state_locale(ctx.state))


async def handle_airtime_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    locale = _state_locale(ctx.state)
    await _handle_purchase_task(
        task,
        task_id,
        ctx,
        worker_name="airtime",
        worker_missing_log_key="airtime_worker_missing",
        worker_missing_error_message=render_message("orchestrator.error.airtime_worker_unavailable", locale),
        default_error=render_message("orchestrator.error.airtime_purchase_failed", locale),
        include_channel=True,
    )


async def _handle_purchase_task(
    task: Any,
    task_id: str,
    ctx: ExecutionContext,
    *,
    worker_name: str,
    worker_missing_log_key: str,
    worker_missing_error_message: str,
    default_error: str,
    include_channel: bool,
) -> None:
    worker = _get_worker(
        ctx.services,
        worker_name,
        task,
        log_key=worker_missing_log_key,
        error_message=worker_missing_error_message,
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    required_fields: list[str] = []
    previous_response: str | None = None
    if ctx.state.last_interrupt and task_id in ctx.state.last_interrupt.task_ids:
        raw_required_fields = ctx.state.last_interrupt.fields_by_task.get(task_id, [])
        required_fields = [field for field in raw_required_fields if isinstance(field, str)]
        previous_response = ctx.state.last_interrupt.prompt

    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
    }
    if include_channel:
        context_data["channel"] = ctx.state.channel

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
        default_error=default_error,
    )


async def handle_query_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
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
        "stashed_query_session": ctx.state.stashed_query_session,
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
    )

    _apply_result_patch(task, result)
    if result.patch and isinstance(result.patch, dict) and result.patch.get("restored_from_stashed_query_session"):
        ctx.agg.updates["stashed_query_session"] = None
    handoff_payload = None
    if result.patch and isinstance(result.patch, dict):
        candidate = result.patch.get("query_transfer_handoff")
        if isinstance(candidate, dict):
            handoff_payload = candidate

    if result.outcome == TransactionOutcome.OK:
        task.stage = TaskStage.COMPLETED
        if result.response:
            task.payload["result"] = result.response
            ctx.agg.say(result.response)

        if handoff_payload:
            transfer_payload = dict(handoff_payload)
            transfer_payload.setdefault("action", "send_money")
            transfer_payload.setdefault("instruction", "Resend the selected transaction")
            transfer_payload.setdefault("message", ctx.state.last_message_text or "Resend the selected transaction")
            transfer_payload.setdefault("skip_extraction", True)

            tasks = cast(dict[str, Any], ctx.agg.updates.get("tasks", ctx.state.tasks))
            transfer_task_id = _next_query_handoff_transfer_task_id(tasks)
            tasks[transfer_task_id] = TaskSpec(
                id=transfer_task_id,
                type="transfer",
                stage=TaskStage.DRAFT,
                payload=transfer_payload,
            )
            ctx.agg.updates["tasks"] = tasks

            waves = list(cast(list[list[str]], ctx.agg.updates.get("waves", ctx.state.waves)))
            insert_index = min(ctx.state.current_wave_index + 1, len(waves))
            waves.insert(insert_index, [transfer_task_id])
            ctx.agg.updates["waves"] = waves

            if not result.response:
                ctx.agg.say("Okay. I will resend that transfer now.")

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        if result.response:
            ctx.agg.add_prompt(result.response, task_id)
            ctx.agg.add_missing_fields(task_id, ["clarification"])

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        task.payload["error"] = result.error or render_message(
            "orchestrator.error.query_processing_failed",
            _state_locale(ctx.state),
        )
        ctx.agg.say(result.response or render_message("query.error.general", _state_locale(ctx.state)))

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

        ctx.agg.updates["session_stack"] = stack


async def handle_data_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    locale = _state_locale(ctx.state)
    await _handle_purchase_task(
        task,
        task_id,
        ctx,
        worker_name="data",
        worker_missing_log_key="data_worker_missing",
        worker_missing_error_message=render_message("orchestrator.error.data_worker_unavailable", locale),
        default_error=render_message("orchestrator.error.data_purchase_failed", locale),
        include_channel=False,
    )


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
    worker = _get_worker(
        ctx.services,
        "support",
        task,
        log_key="support_worker_missing",
        error_message=render_message("orchestrator.error.support_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    user_msg = ctx.state.last_message_text
    context_data = {
        "phone_number": ctx.state.phone_number,
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


async def handle_orchestrator_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    """Handle orchestrator tasks (e.g. resumption)."""
    del task_id
    action = task.payload.get("action")
    locale = _state_locale(ctx.state)
    if action not in {"resume_session", "dismiss_resume_session"}:
        return

    if not ctx.state.stashed_sessions:
        no_stash_message = render_message("orchestrator.session.no_stashed", locale)
        ctx.agg.say(no_stash_message)
        task.stage = TaskStage.FAILED
        task.payload["error"] = no_stash_message
        return

    # Pop last stashed session for both accept/decline actions.
    last_session = ctx.state.stashed_sessions[-1]
    remaining_stash = ctx.state.stashed_sessions[:-1]
    intent = str(last_session.get("intent", render_message("orchestrator.session.default_intent", locale)))
    ctx.agg.updates["stashed_sessions"] = remaining_stash
    ctx.agg.updates["context_frames"] = _clear_resume_prompt_frames(ctx.state.context_frames)

    if action == "resume_session":
        p_interrupt = last_session.get("pending_interrupt")
        logger.info("resuming_session", intent=intent, has_interrupt=bool(p_interrupt))
        restored_tasks = cast(dict[str, Any], last_session["tasks"])

        # Restore stashed state.
        ctx.agg.updates["tasks"] = restored_tasks
        ctx.agg.updates["waves"] = last_session["waves"]
        ctx.agg.updates["current_wave_index"] = last_session["current_wave_index"]
        ctx.agg.updates["pending_interrupt"] = None
        ctx.agg.updates["last_interrupt"] = p_interrupt
        # Drop resume acceptance text so resumed workers don't treat it as slot input.
        ctx.agg.updates["last_message_text"] = None
        task.stage = TaskStage.COMPLETED
        return

    logger.info("resume_session_declined", intent=intent)
    ctx.agg.say(render_message("orchestrator.session.resume_declined", locale))
    task.stage = TaskStage.COMPLETED
