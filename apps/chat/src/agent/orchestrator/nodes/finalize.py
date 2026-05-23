from __future__ import annotations

import time
import uuid
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referent_memory import (
    forget_stashed_referents,
    remember_referents_from_completed_task,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.shared.query_contracts import SelectionPayload
from shared.formatters.transaction_copy import format_amount_compact
from shared.formatters.transaction_summary import format_multi_action_summary
from shared.i18n import (
    LocaleManager,
    render_cancelled_prompt,
    render_generic_capability_blocked,
    render_message,
    render_text,
)
from shared.receipts.choice import build_receipt_choice_intent
from shared.utils.logging import get_logger
from shared.utils.user_error import safe_user_error_message

logger = get_logger(__name__)
TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}
TERMINAL_TASK_STAGES = {TaskStage.COMPLETED.value, TaskStage.FAILED.value, TaskStage.CANCELLED.value}
STASH_RESUME_TTL_SECONDS = 1800
ASYNC_RECEIPT_STATUSES = {"queued", "processing", "pending"}
COMPLETED_TRANSACTION_FRAME_TTL_SECONDS = 900


def _extract_interrupt_task_ids(pending_interrupt: Any) -> list[str]:
    if isinstance(pending_interrupt, dict):
        raw_task_ids = pending_interrupt.get("task_ids")
    else:
        raw_task_ids = getattr(pending_interrupt, "task_ids", None)
    if not isinstance(raw_task_ids, list):
        return []
    return [str(task_id) for task_id in raw_task_ids if isinstance(task_id, str)]


def _extract_task_stage_value(task: Any) -> str | None:
    if isinstance(task, TaskSpec):
        stage = task.stage
    elif isinstance(task, dict):
        stage = task.get("stage")
    else:
        stage = getattr(task, "stage", None)

    if isinstance(stage, TaskStage):
        return stage.value
    if isinstance(stage, str):
        return stage
    return None


def _is_resumable_stashed_session(stashed_session: dict[str, Any], *, now_ts: int) -> bool:
    stashed_at_ts = stashed_session.get("stashed_at_ts")
    if not isinstance(stashed_at_ts, int):
        return False
    if stashed_at_ts + STASH_RESUME_TTL_SECONDS <= now_ts:
        return False

    pending_interrupt = stashed_session.get("pending_interrupt")
    task_ids = _extract_interrupt_task_ids(pending_interrupt)
    if not task_ids:
        return False

    tasks = stashed_session.get("tasks")
    if not isinstance(tasks, dict):
        return False

    for task_id in task_ids:
        stage = _extract_task_stage_value(tasks.get(task_id))
        if stage and stage not in TERMINAL_TASK_STAGES:
            return True
    return False


def _stashed_session_id(stashed_session: dict[str, Any]) -> str | None:
    stash_id = stashed_session.get("stash_id")
    return str(stash_id).strip() if stash_id else None


def _stashed_task_type(task: Any) -> str:
    if isinstance(task, TaskSpec):
        return task.type
    if isinstance(task, dict):
        return str(task.get("type") or "").strip()
    return str(getattr(task, "type", "") or "").strip()


def _stashed_task_payload(task: Any) -> dict[str, Any]:
    payload = task.payload if isinstance(task, TaskSpec) else task.get("payload") if isinstance(task, dict) else None
    return payload if isinstance(payload, dict) else {}


def _safe_resume_amount(value: Any) -> str:
    if value is None or value == "":
        return ""
    normalized_value = value.replace(",", "") if isinstance(value, str) else value
    try:
        amount = float(normalized_value)
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    return format_amount_compact(amount)


def _build_resume_prompt(session: dict[str, Any], *, locale: str) -> str:
    task_type = str(session.get("intent") or "").strip().lower()
    tasks = session.get("tasks")
    candidate_payload: dict[str, Any] = {}
    if isinstance(tasks, dict):
        for task in tasks.values():
            current_type = _stashed_task_type(task)
            if current_type in TRANSACTION_TASK_TYPES:
                task_type = current_type
                candidate_payload = _stashed_task_payload(task)
                break

    if task_type == "transfer":
        amount = _safe_resume_amount(candidate_payload.get("amount"))
        recipient = _first_non_empty(
            candidate_payload.get("recipient_resolved_name"),
            candidate_payload.get("recipient_name"),
        )
        if amount and recipient:
            return render_message(
                "orchestrator.finalize.resume_prompt_transfer_specific",
                locale,
                {"amount": amount, "recipient": recipient},
            )
        return render_message("orchestrator.finalize.resume_prompt_transfer_generic", locale)

    if task_type == "airtime":
        amount = _safe_resume_amount(candidate_payload.get("amount"))
        phone = _first_non_empty(
            candidate_payload.get("recipient_phone"),
            candidate_payload.get("phone_number"),
            candidate_payload.get("phone"),
        )
        if amount and phone:
            return render_message(
                "orchestrator.finalize.resume_prompt_airtime_specific",
                locale,
                {"amount": amount, "phone": phone},
            )
        return render_message("orchestrator.finalize.resume_prompt_airtime_generic", locale)

    if task_type == "data":
        amount = _safe_resume_amount(candidate_payload.get("amount"))
        phone = _first_non_empty(
            candidate_payload.get("target_phone"),
            candidate_payload.get("recipient_phone"),
            candidate_payload.get("phone_number"),
            candidate_payload.get("phone"),
        )
        if amount and phone:
            return render_message(
                "orchestrator.finalize.resume_prompt_data_specific",
                locale,
                {"amount": amount, "phone": phone},
            )
        return render_message("orchestrator.finalize.resume_prompt_data_generic", locale)

    intent = session.get("intent", render_message("orchestrator.session.default_intent", locale))
    return render_message("orchestrator.finalize.resume_prompt", locale, {"intent": intent})


def _has_live_resume_prompt_frame(frames: list[ContextFrame]) -> bool:
    now = int(time.time())
    for frame in frames:
        if frame.frame_type != ContextFrameType.GENERIC:
            continue
        if frame.created_at_ts + frame.ttl_seconds <= now:
            continue
        if any(item.data.get("resume_prompt") is True for item in frame.items):
            return True
    return False


def _receipt_status(task: TaskSpec) -> str:
    receipt = task.payload.get("receipt")
    if not isinstance(receipt, dict):
        return ""
    raw_status = receipt.get("status")
    if not isinstance(raw_status, str):
        return ""
    return raw_status.strip().lower()


def _is_async_transfer_task(task: TaskSpec) -> bool:
    return task.type == "transfer" and _receipt_status(task) in ASYNC_RECEIPT_STATUSES


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _safe_completed_transaction_data(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(fragment in lowered for fragment in ("pin", "otp", "password", "token", "secret", "auth")):
            continue
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key_text] = value
    return safe


def _completed_transaction_reference(task: TaskSpec, payload: dict[str, Any], receipt: dict[str, Any]) -> str:
    return _first_non_empty(
        payload.get("transaction_id"),
        payload.get("transaction_reference"),
        payload.get("reference"),
        receipt.get("transaction_id"),
        receipt.get("transaction_reference"),
        receipt.get("reference"),
        receipt.get("id"),
        payload.get("idempotency_key"),
        task.id,
    )


def _completed_transfer_recipient_payload(task: TaskSpec, recipient: dict[str, Any], index: int) -> dict[str, Any]:
    payload = task.payload
    amount = recipient.get("amount", payload.get("amount"))
    recipient_name = _first_non_empty(
        recipient.get("recipient_name"),
        recipient.get("alias"),
        payload.get("recipient_name"),
    )
    resolved_name = _first_non_empty(
        recipient.get("recipient_resolved_name"),
        recipient.get("name"),
        payload.get("recipient_resolved_name"),
        recipient_name,
    )
    recipient_bank = _first_non_empty(
        recipient.get("recipient_bank_name"),
        recipient.get("bank_name"),
        payload.get("recipient_bank_name"),
    )
    recipient_bank_code = _first_non_empty(
        recipient.get("recipient_bank_code"),
        recipient.get("bank_code"),
        payload.get("recipient_bank_code"),
    )
    recipient_account = _first_non_empty(
        recipient.get("recipient_account"),
        recipient.get("account"),
        payload.get("recipient_account"),
    )
    return {
        "task_id": f"{task.id}:{index}",
        "task_type": "transfer",
        "type": "transfer",
        "transaction_type": "transfer",
        "amount": amount,
        "status": _receipt_status(task) or payload.get("final_status") or "success",
        "recipient_name": recipient_name,
        "recipient_resolved_name": resolved_name,
        "counterparty": resolved_name or recipient_name,
        "recipient_bank_name": recipient_bank,
        "recipient_bank_code": recipient_bank_code,
        "bank_name": recipient_bank,
        "bank_code": recipient_bank_code,
        "bank": recipient_bank,
        "recipient_account": recipient_account,
        "account": recipient_account,
        "source_account_id": payload.get("source_account_id"),
        "source_bank_name": payload.get("source_bank_name"),
        "source_account_number": payload.get("source_account_number"),
        "source_account_last4": _string(payload.get("source_account_number"))[-4:],
        "narration": recipient.get("narration") or payload.get("narration"),
        "reference": payload.get("transaction_id") or payload.get("idempotency_key"),
        "date": payload.get("date"),
    }


def _completed_transaction_entity_payloads(task: TaskSpec) -> list[dict[str, Any]]:
    payload = task.payload
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
    if task.type == "transfer":
        recipients = payload.get("recipients")
        if isinstance(recipients, list) and recipients:
            return [
                _completed_transfer_recipient_payload(task, recipient, idx)
                for idx, recipient in enumerate(recipients, 1)
                if isinstance(recipient, dict)
            ]
        return [_completed_transfer_recipient_payload(task, {}, 1)]

    if task.type == "airtime":
        phone = _first_non_empty(
            payload.get("recipient_phone"),
            payload.get("phone_number"),
            payload.get("recipientPhone"),
            payload.get("phone"),
            receipt.get("phone"),
        )
        return [
            {
                "task_id": task.id,
                "task_type": "airtime",
                "type": "airtime",
                "transaction_type": "airtime",
                "amount": payload.get("amount"),
                "status": _receipt_status(task) or payload.get("final_status") or "success",
                "phone": phone,
                "recipient_phone": phone,
                "counterparty": phone,
                "network": _first_non_empty(payload.get("network"), receipt.get("network")),
                "source_account_id": payload.get("source_account_id"),
                "source_bank_name": payload.get("source_bank_name"),
                "source_account_number": payload.get("source_account_number"),
                "reference": _completed_transaction_reference(task, payload, receipt),
                "date": _first_non_empty(payload.get("date"), receipt.get("date")),
                "description": _first_non_empty(receipt.get("message"), receipt.get("description")),
            }
        ]

    if task.type == "data":
        phone = _first_non_empty(payload.get("target_phone"), payload.get("phone_number"), payload.get("phone"))
        return [
            {
                "task_id": task.id,
                "task_type": "data",
                "type": "data",
                "transaction_type": "data",
                "amount": payload.get("amount"),
                "status": _receipt_status(task) or payload.get("final_status") or "success",
                "phone": phone,
                "recipient_phone": phone,
                "counterparty": phone,
                "network": payload.get("network"),
                "plan_name": payload.get("plan_name"),
                "source_account_id": payload.get("source_account_id"),
                "source_bank_name": payload.get("source_bank_name"),
                "source_account_number": payload.get("source_account_number"),
                "reference": _completed_transaction_reference(task, payload, receipt),
                "date": _first_non_empty(payload.get("date"), receipt.get("date")),
                "description": _first_non_empty(receipt.get("message"), receipt.get("description")),
            }
        ]

    return []


def _completed_transaction_label(data: dict[str, Any]) -> str:
    task_type = _string(data.get("task_type"))
    amount = format_amount_compact(data.get("amount"))
    if task_type == "transfer":
        recipient = _first_non_empty(data.get("recipient_resolved_name"), data.get("recipient_name"), "recipient")
        return f"{amount} transfer to {recipient}"
    if task_type == "airtime":
        phone = _first_non_empty(data.get("phone"), data.get("recipient_phone"), "phone number")
        return f"{amount} airtime for {phone}"
    if task_type == "data":
        phone = _first_non_empty(data.get("phone"), data.get("recipient_phone"), "phone number")
        plan = _string(data.get("plan_name"))
        return f"{amount} data for {phone}" if not plan else f"{plan} data for {phone}"
    return f"{amount} transaction"


def _selection_payload_for_completed_transaction(data: dict[str, Any]) -> SelectionPayload:
    label = _completed_transaction_label(data)
    task_type = _string(data.get("task_type")) or "transaction"
    handoff_payload = None
    if task_type == "transfer":
        handoff_payload = {
            "recipient_name": data.get("recipient_name"),
            "recipient_resolved_name": data.get("recipient_resolved_name"),
            "recipient_account": data.get("recipient_account"),
            "recipient_bank_name": data.get("recipient_bank_name"),
            "recipient_bank_code": data.get("recipient_bank_code"),
            "amount": data.get("amount"),
            "narration": data.get("narration"),
            "source_account_id": data.get("source_account_id"),
            "source_bank_name": data.get("source_bank_name"),
            "source_account_number": data.get("source_account_number"),
        }
    return SelectionPayload(
        selection_kind="transaction",
        entity_type=task_type,
        entity_id=_string(data.get("reference") or data.get("task_id")) or None,
        label=label,
        fact_capabilities=["date", "amount", "bank", "counterparty"],
        handoff_payload=handoff_payload,
    )


def _build_completed_transaction_frame(
    *,
    visible_tasks: list[TaskSpec],
    source_message_id: str | None,
) -> ContextFrame | None:
    transaction_tasks = [task for task in visible_tasks if task.type in TRANSACTION_TASK_TYPES]
    if not transaction_tasks:
        return None

    entities: list[ContextEntity] = []
    has_receipt = False
    for task in transaction_tasks:
        has_receipt = has_receipt or isinstance(task.payload.get("receipt"), dict)
        for raw_data in _completed_transaction_entity_payloads(task):
            data = _safe_completed_transaction_data(raw_data)
            label = _completed_transaction_label(data)
            selection_payload = _selection_payload_for_completed_transaction(data)
            entities.append(
                ContextEntity(
                    entity_type=EntityType.TRANSACTION,
                    entity_id=selection_payload.entity_id or _string(data.get("task_id")) or None,
                    label=label,
                    data=data,
                    selection_payload=selection_payload,
                )
            )

    if not entities:
        return None

    frame_type = ContextFrameType.TRANSACTION_LIST
    if len(entities) == 1:
        frame_type = ContextFrameType.RECEIPT if has_receipt else ContextFrameType.TRANSACTION_DETAIL

    return ContextFrame(
        frame_id=f"completed_transaction_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        frame_type=frame_type,
        items=entities,
        focus_index=0,
        source_message_id=source_message_id,
        created_at_ts=int(time.time()),
        ttl_seconds=COMPLETED_TRANSACTION_FRAME_TTL_SECONDS,
    )


def _append_transfer_processing_message(task: TaskSpec, outbox: list[dict[str, Any]], locale: str) -> None:
    display_name = (
        task.payload.get("recipient_resolved_name")
        or task.payload.get("recipient_name")
        or render_message("orchestrator.finalize.recipient_fallback", locale)
    )
    amount = task.payload.get("amount", "")
    outbox.append(
        {
            "type": "say",
            "text": render_message(
                "orchestrator.finalize.transfer_processing",
                locale,
                {"amount": f"{amount:,.2f}", "display_name": display_name},
            ),
        }
    )


async def _enqueue_finalize_transfer_receipt(
    *,
    task: TaskSpec,
    state: OrchestratorState,
    config: RunnableConfig,
    locale: str,
) -> dict[str, Any] | None:
    configurable = config.get("configurable", {})
    transaction_reference = task.payload.get("transaction_id")
    if not isinstance(transaction_reference, str) or not transaction_reference.strip():
        return None

    beneficiary_suggestion_message: str | None = None
    suggestion_service = configurable.get("beneficiary_suggestion_service")
    recipient_account = task.payload.get("recipient_account")
    recipient_bank_code = task.payload.get("recipient_bank_code")
    if suggestion_service is not None and recipient_account:
        beneficiary_suggestion_message = await suggestion_service.check_and_suggest_beneficiary(
            phone_number=state.phone_number,
            beneficiary_type="transfer",
            recipient_data={
                "account_number": recipient_account,
                "bank_code": recipient_bank_code,
                "bank_name": task.payload.get("recipient_bank_name"),
                "recipient_bank_code_provider": task.payload.get("recipient_bank_code_provider"),
                "recipient_resolution_provider": task.payload.get("recipient_resolution_provider"),
                "name": task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name"),
                "original_alias": task.payload.get("recipient_name"),
            },
            transaction_id=transaction_reference,
            send_message=False,
            channel=state.channel,
            locale=locale,
        )

    payload: dict[str, Any] = {
        "phone_number": state.phone_number,
        "channel": state.channel,
        "channel_identity": state.channel_identity,
        "transfer_data": {
            "amount": task.payload.get("amount"),
            "source": {
                "name": task.payload.get("source_bank_name"),
                "account_name": task.payload.get("source_account_name"),
                "account_number": task.payload.get("source_account_number"),
            },
            "recipient": {
                "name": task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name"),
                "account_number": recipient_account,
                "bank_name": task.payload.get("recipient_bank_name"),
            },
            "narration": task.payload.get("narration"),
            "channel": state.channel,
            "session_id": task.payload.get("idempotency_key") or transaction_reference,
        },
        "transaction_reference": transaction_reference,
        "signal_key": f"receipt:{uuid.uuid4()}",
    }
    if beneficiary_suggestion_message:
        payload["beneficiary_suggestion_message"] = beneficiary_suggestion_message

    return build_receipt_choice_intent(payload, locale).to_dict()


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Final Step. Generate response and queue receipts."""
    outbox = list(state.outbox)
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    completed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.COMPLETED]
    failed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.FAILED]
    cancelled_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.CANCELLED]

    suppress_empty_fallback = False
    if completed_tasks:
        suppress_empty_fallback = await _handle_completed_tasks(
            completed_tasks=completed_tasks,
            state=state,
            config=config,
            outbox=outbox,
        )

    for task in failed_tasks:
        if task.payload.get("capability_blocked"):
            message = task.payload.get("error") or render_generic_capability_blocked(locale)
            outbox.append({"type": "say", "text": message})
        elif task.payload.get("is_pending_mandate"):
            error_text = safe_user_error_message(task.payload.get("error"), task_type=task.type, locale=locale)
            outbox.append({"type": "say", "text": error_text})
        else:
            error_text = safe_user_error_message(task.payload.get("error"), task_type=task.type, locale=locale)
            outbox.append(
                {
                    "type": "say",
                    "text": render_message("orchestrator.finalize.failed_prefix", locale, {"error": error_text}),
                }
            )

    if cancelled_tasks:
        outbox.append({"type": "say", "text": render_cancelled_prompt(locale)})

    context_updates = {}
    has_completed_non_transaction = any(task.type not in TRANSACTION_TASK_TYPES for task in completed_tasks)
    now_ts = int(time.time())

    for task in completed_tasks:
        remember_referents_from_completed_task(state, task)
    if completed_tasks:
        context_updates["referent_memory"] = state.referent_memory

    visible_completed_tasks = [task for task in completed_tasks if not task.payload.get("skip_finalize_summary")]
    completed_transaction_frame = _build_completed_transaction_frame(
        visible_tasks=visible_completed_tasks,
        source_message_id=state.last_message_id,
    )
    if completed_transaction_frame is not None:
        OrchestratorContextManager().push_frame(state, completed_transaction_frame)
        context_updates["context_frames"] = state.context_frames
        context_updates["referent_memory"] = state.referent_memory

    resumable_stashed_sessions = [
        session
        for session in state.stashed_sessions
        if isinstance(session, dict)
        and _is_resumable_stashed_session(cast(dict[str, Any], session), now_ts=now_ts)
    ]
    if len(resumable_stashed_sessions) != len(state.stashed_sessions):
        stale_stash_ids = {
            stash_id
            for session in state.stashed_sessions
            if isinstance(session, dict)
            and session not in resumable_stashed_sessions
            for stash_id in [_stashed_session_id(session)]
            if stash_id
        }
        if stale_stash_ids:
            forget_stashed_referents(state, stale_stash_ids)
            context_updates["referent_memory"] = state.referent_memory
        context_updates["stashed_sessions"] = resumable_stashed_sessions

    if (
        resumable_stashed_sessions
        and has_completed_non_transaction
        and not _has_live_resume_prompt_frame(state.context_frames)
    ):
        last_session = resumable_stashed_sessions[-1]
        intent = last_session.get("intent", render_message("orchestrator.session.default_intent", locale))
        resume_prompt = _build_resume_prompt(last_session, locale=locale)

        if outbox and outbox[-1].get("type") == "say":
            outbox[-1]["text"] += f"\n\n{resume_prompt}"
        else:
            outbox.append({"type": "say", "text": resume_prompt})

        frame = ContextFrame(
            frame_id=str(uuid.uuid4()),
            frame_type=ContextFrameType.GENERIC,
            items=[
                ContextEntity(
                    entity_id="resumption_prompt",
                    label=f"Resume {intent}",
                    entity_type=EntityType.GENERIC,
                    data={
                        "intent": intent,
                        "resume_prompt": True,
                        "stash_id": _stashed_session_id(last_session),
                    },
                )
            ],
            created_at_ts=int(time.time()),
            ttl_seconds=300,
        )
        current_frames = list(state.context_frames)
        current_frames.append(frame)
        context_updates["context_frames"] = current_frames

    return {
        "outbox": outbox,
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "pending_interrupt": None,
        "last_interrupt": None,
        "pin_verified": False,
        "last_callback": None,
        "session_stack": [],
        "active_domain": None,
        "suppress_empty_fallback": suppress_empty_fallback,
        **context_updates,
    }


async def _handle_completed_tasks(
    completed_tasks: list[TaskSpec],
    state: OrchestratorState,
    config: RunnableConfig,
    outbox: list[dict[str, Any]],
) -> bool:
    """Handle completed tasks and generate receipts or summaries."""
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    visible_tasks = [task for task in completed_tasks if not task.payload.get("skip_finalize_summary")]
    if not visible_tasks:
        return False

    logger.info("handling_completed_tasks", count=len(completed_tasks), tasks=[t.type for t in completed_tasks])
    for t in visible_tasks:
        logger.info(
            "completed_task_detail",
            type=t.type,
            has_receipt="receipt" in t.payload,
            payload_keys=list(t.payload.keys()),
        )
    transaction_visible_tasks = [task for task in visible_tasks if task.type in TRANSACTION_TASK_TYPES]
    async_transaction_tasks = [
        task for task in transaction_visible_tasks if _receipt_status(task) in ASYNC_RECEIPT_STATUSES
    ]
    async_transfer_tasks = [task for task in transaction_visible_tasks if _is_async_transfer_task(task)]
    is_single_transfer = (
        len(visible_tasks) == 1
        and visible_tasks[0].type == "transfer"
        and not visible_tasks[0].payload.get("is_batch", False)
        and len(visible_tasks[0].payload.get("recipients", [])) <= 1
    )

    read_only_task_types = {"account", "query", "faq", "support", "beneficiary", "schedule"}
    all_read_only = all(task.type in read_only_task_types for task in visible_tasks)

    is_async_transaction = len(visible_tasks) == 1 and visible_tasks[0].type in ("airtime", "data")

    if is_single_transfer:
        task = visible_tasks[0]
        if _receipt_status(task) in ASYNC_RECEIPT_STATUSES:
            logger.info(
                "finalize_single_transfer_status_deferred_to_executor",
                task_id=task.id,
                receipt_status=_receipt_status(task),
            )
            return True
        else:
            receipt_offer = await _enqueue_finalize_transfer_receipt(
                task=task,
                state=state,
                config=config,
                locale=locale,
            )
            if receipt_offer:
                outbox.append(receipt_offer)

    elif len(async_transaction_tasks) > 1:
        logger.info(
            "finalize_async_batch_processing",
            count=len(async_transaction_tasks),
            task_ids=[task.id for task in async_transaction_tasks],
        )
        outbox.append({"type": "say", "text": render_text("Your transactions are being processed.", locale)})

    elif async_transfer_tasks:
        logger.info(
            "finalize_async_transfer_processing",
            count=len(async_transfer_tasks),
            task_ids=[task.id for task in async_transfer_tasks],
        )
        for task in async_transfer_tasks:
            _append_transfer_processing_message(task, outbox, locale)

    elif is_async_transaction:
        task = visible_tasks[0]
        receipt = task.payload.get("receipt", {})
        status = receipt.get("status", "").title()
        message = receipt.get("message", render_message("orchestrator.finalize.transaction_completed", locale))

        logger.info("generating_async_receipt", task_type=task.type, status=status, message=message)

        outbox.append(
            {
                "type": "say",
                "text": render_message(
                    "orchestrator.finalize.async_status_message",
                    locale,
                    {"status": status, "message": message},
                ),
            }
        )

    elif all_read_only:
        pass
    else:
        summary_source = transaction_visible_tasks or visible_tasks
        summary_text = format_multi_action_summary(summary_source, locale=locale)
        summary_outbox: dict[str, Any] = {"type": "say", "text": summary_text}
        if actionable_payload := build_actionable_payload_for_tasks(summary_source):
            summary_outbox["actionable_payload"] = actionable_payload
        outbox.append(summary_outbox)

    return False
