import re
import time
from dataclasses import dataclass
from hashlib import sha1
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referent_memory import build_resolved_referents, forget_stashed_referents
from apps.chat.src.agent.orchestrator.context.surface_adapter import build_context_frame_from_surface_view
from apps.chat.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    ActiveSession,
    FAQOutcome,
    SupportOutcome,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.shared.query_contracts import FocusedReferent, SelectionPayload
from apps.chat.src.agent.shared.routing_signals import looks_like_transaction_replay_modifier_request
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger
from shared.utils.serialization import sqlalchemy_to_dict

logger = get_logger(__name__)

SessionState = Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH", "RUNNING"]
_RECIPIENT_PRONOUN_TOKENS = {"her", "him", "them", "that", "it", "this", "previous"}
_TERMINAL_TRANSACTION_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}


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
    current_wave_task_ids: list[str] | None = None


def _state_locale(state: OrchestratorState) -> str:
    return cast(str, LocaleManager.normalize(state.loaded_context.get("language")).value)


def _stamp_async_group_metadata(task: Any, ctx: ExecutionContext) -> None:
    if task.type not in {"transfer", "airtime", "data"}:
        return

    transaction_types = {"transfer", "airtime", "data"}
    current_wave_task_ids = ctx.current_wave_task_ids or []
    current_wave_transaction_task_ids = [
        task_id
        for task_id in current_wave_task_ids
        if (wave_task := ctx.state.tasks.get(task_id)) is not None and wave_task.type in transaction_types
    ]

    previous_group_id = task.payload.get("async_group_id")
    candidate_task_ids = set(current_wave_transaction_task_ids)
    if previous_group_id:
        candidate_task_ids.update(
            task_id
            for task_id, sibling in ctx.state.tasks.items()
            if sibling.type in transaction_types
            and sibling.stage not in _TERMINAL_TRANSACTION_STAGES
            and sibling.payload.get("async_group_id") == previous_group_id
        )

    transaction_task_ids = [task_id for task_id in ctx.state.tasks if task_id in candidate_task_ids]
    for task_id in current_wave_transaction_task_ids:
        if task_id not in transaction_task_ids:
            transaction_task_ids.append(task_id)

    if not transaction_task_ids:
        return

    group_size = len(transaction_task_ids)
    group_kind: Literal["single", "multi_transfer", "mixed_batch"]
    if group_size == 1:
        group_kind = "single"
    elif all(ctx.state.tasks[task_id].type == "transfer" for task_id in transaction_task_ids):
        group_kind = "multi_transfer"
    else:
        group_kind = "mixed_batch"

    group_fingerprint = "|".join(
        [
            str(ctx.state.last_message_id or ""),
            str(ctx.state.current_wave_index),
            *transaction_task_ids,
        ]
    )
    group_id = sha1(group_fingerprint.encode("utf-8")).hexdigest()[:20]

    for index, task_id in enumerate(transaction_task_ids, start=1):
        grouped_task = ctx.state.tasks.get(task_id)
        if grouped_task is None or grouped_task.type not in transaction_types:
            continue
        grouped_task.payload["async_group_id"] = group_id
        grouped_task.payload["async_group_size"] = group_size
        grouped_task.payload["async_group_kind"] = group_kind
        grouped_task.payload["async_group_index"] = index


def _normalize_beneficiary_rows(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
        else:
            normalized.append(sqlalchemy_to_dict(row))
    return normalized


def _recipient_supports_targeted_beneficiary_lookup(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    return normalized not in _RECIPIENT_PRONOUN_TOKENS


def _normalize_beneficiary_match_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _beneficiary_cache_contains_recipient(beneficiaries: list[dict[str, Any]], recipient_name: str) -> bool:
    requested = _normalize_beneficiary_match_text(recipient_name)
    if not requested:
        return False

    for beneficiary in beneficiaries:
        alias = _normalize_beneficiary_match_text(beneficiary.get("alias"))
        account_name = _normalize_beneficiary_match_text(beneficiary.get("account_name"))
        if alias and (requested in alias or alias in requested):
            return True
        if account_name and (requested in account_name or account_name in requested):
            return True
    return False


def _is_resume_prompt_frame(frame: ContextFrame) -> bool:
    """Return True if a context frame represents a resume prompt."""
    return any(item.data.get("resume_prompt") is True for item in frame.items)


def _clear_resume_prompt_frames(frames: list[ContextFrame]) -> list[ContextFrame]:
    """Remove stale resume prompt frames after accept/decline."""
    return [frame for frame in frames if not _is_resume_prompt_frame(frame)]


def _push_query_followup_referent_frame(
    ctx: ExecutionContext,
    referent: FocusedReferent | dict[str, Any],
) -> None:
    referent_data = referent.model_dump() if hasattr(referent, "model_dump") else referent
    label = str(referent_data.get("label") or referent_data.get("recipient_name") or "").strip()
    if not label:
        return

    entity_id = str(referent_data.get("entity_id") or "").strip() or None
    recipient_name = str(referent_data.get("recipient_name") or label).strip()
    account_number = str(referent_data.get("recipient_account") or "").strip() or None
    bank_name = str(referent_data.get("recipient_bank_name") or "").strip() or None
    bank_code = str(referent_data.get("recipient_bank_code") or "").strip() or None
    resolved_name = str(referent_data.get("recipient_resolved_name") or recipient_name).strip()
    selection_payload = referent.selection_payload if isinstance(referent, FocusedReferent) else None
    if selection_payload is None:
        selection_payload = SelectionPayload(
            selection_kind="transaction",
            entity_type="transaction",
            entity_id=entity_id,
            label=label,
            fact_capabilities=["date", "amount", "bank", "counterparty"],
        )

    entity = ContextEntity(
        entity_type=EntityType.BENEFICIARY,
        entity_id=entity_id,
        label=label,
        data={
            "id": entity_id,
            "alias": recipient_name,
            "account_name": resolved_name,
            "account_number": account_number,
            "bank_name": bank_name,
            "bank_code": bank_code,
            "beneficiary_type": "transfer",
            "bank": bank_name,
            "account": account_number,
        },
        selection_payload=selection_payload,
        focused_referent=referent if isinstance(referent, FocusedReferent) else None,
    )
    frame = ContextFrame(
        frame_id=f"query_beneficiary_{int(time.time())}",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=[entity],
        focus_index=0,
        created_at_ts=int(time.time()),
        source_message_id=ctx.state.last_message_id,
    )
    OrchestratorContextManager().push_frame(ctx.state, frame)
    ctx.agg.updates["context_frames"] = ctx.state.context_frames
    ctx.agg.updates["referent_memory"] = ctx.state.referent_memory


def _push_account_list_frame(ctx: ExecutionContext, accounts: list[dict[str, Any]]) -> None:
    if not accounts:
        return

    entities: list[ContextEntity] = []
    for idx, account in enumerate(accounts, 1):
        bank_name = str(account.get("bank_name") or "Account").strip()
        account_number = str(account.get("account_number") or "").strip()
        last4 = account_number[-4:] if account_number else str(idx)
        label = f"{bank_name} (...{last4})"
        entities.append(
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id=str(account.get("account_id") or account.get("id") or idx),
                label=label,
                data=account,
            )
        )

    frame = ContextFrame(
        frame_id=f"account_list_{int(time.time())}",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=entities,
        focus_index=0,
        created_at_ts=int(time.time()),
        source_message_id=ctx.state.last_message_id,
    )
    OrchestratorContextManager().push_frame(ctx.state, frame)
    ctx.agg.updates["context_frames"] = ctx.state.context_frames
    ctx.agg.updates["referent_memory"] = ctx.state.referent_memory
    logger.info("context_frame_pushed", type="account_list", count=len(entities))


def _push_schedule_list_frame(ctx: ExecutionContext, items: list[dict[str, Any]]) -> None:
    if not items:
        return

    entities: list[ContextEntity] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            data = {}
        label = str(item.get("label") or data.get("summary") or f"Scheduled transaction {idx}").strip()
        entities.append(
            ContextEntity(
                entity_type=EntityType.GENERIC,
                entity_id=str(item.get("entity_id") or data.get("schedule_id") or idx),
                label=label,
                data=data,
            )
        )

    if not entities:
        return

    frame = ContextFrame(
        frame_id=f"schedule_list_{int(time.time())}",
        frame_type=ContextFrameType.SCHEDULE_LIST,
        items=entities,
        focus_index=0,
        created_at_ts=int(time.time()),
        source_message_id=ctx.state.last_message_id,
    )
    OrchestratorContextManager().push_frame(ctx.state, frame)
    ctx.agg.updates["context_frames"] = ctx.state.context_frames
    ctx.agg.updates["referent_memory"] = ctx.state.referent_memory
    logger.info("context_frame_pushed", type="schedule_list", count=len(entities))


def _push_query_surface_frame(ctx: ExecutionContext, query_result: Any) -> None:
    if query_result is None:
        return

    surface_view = getattr(query_result, "surface_view", None)
    if surface_view is None:
        try:
            from apps.chat.src.agent.graphs.query.presentation.surface_builder import build_surface_view

            surface_view = build_surface_view(query_result)
        except Exception as exc:
            logger.warning("query_surface_frame_build_failed", error=str(exc))
            return
    if surface_view is None or not getattr(surface_view, "items", None):
        return

    frame = build_context_frame_from_surface_view(
        surface_view,
        source="query",
        source_message_id=ctx.state.last_message_id,
    )
    if frame is None:
        return

    OrchestratorContextManager().push_frame(ctx.state, frame)
    ctx.agg.updates["context_frames"] = ctx.state.context_frames
    ctx.agg.updates["referent_memory"] = ctx.state.referent_memory
    logger.info("context_frame_pushed", type=frame.frame_type.value, count=len(frame.items))


def _query_pagination_actionable_payload(ctx: ExecutionContext, result: Any) -> dict[str, Any] | None:
    if ctx.state.channel != "telegram" or not isinstance(result.patch, dict):
        return None

    query_result = result.patch.get("query_result")
    if query_result is None:
        return None
    surface_view = getattr(query_result, "surface_view", None)
    surface_mode = getattr(surface_view, "mode", None)
    if getattr(surface_mode, "value", surface_mode) != "transaction_list":
        return None

    current_page_raw = result.patch.get("current_page", 0)
    try:
        current_page = int(current_page_raw or 0)
    except (TypeError, ValueError):
        current_page = 0

    has_more = bool(getattr(query_result, "has_more", False))
    buttons: list[dict[str, str]] = []
    if current_page > 0:
        buttons.append({"id": "Previous page", "title": "Back"})
    if has_more:
        buttons.append({"id": "Next page", "title": "Next"})
    if not buttons:
        return None

    return {
        "kind": "query_pagination",
        "current_page": current_page,
        "has_more": has_more,
        "telegram_inline_buttons": buttons,
    }


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
        scoped_user_message = task.payload.pop("pending_user_message", None)
        if isinstance(scoped_user_message, str) and scoped_user_message.strip():
            return scoped_user_message
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
    else:
        confirmation.pop("update_message", None)
    task.payload.pop("transition_acknowledgment", None)
    task.payload.pop("previous_confirmation_snapshot", None)


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
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        if default_error is None:
            task.payload["error"] = result.error
        else:
            task.payload["error"] = result.error or default_error


def _push_data_plan_frame(ctx: ExecutionContext, results: Any) -> None:
    if not isinstance(results, list):
        return
    entities: list[ContextEntity] = []
    for idx, item in enumerate(results[:5], start=1):
        if not isinstance(item, dict):
            continue
        plan_code = str(item.get("plan_code") or item.get("item_code") or item.get("option_id") or "").strip()
        label = str(item.get("plan_name") or item.get("name") or item.get("label") or f"Data plan {idx}").strip()
        data = {
            "plan_code": item.get("plan_code") or item.get("item_code"),
            "item_code": item.get("item_code") or item.get("plan_code"),
            "plan_name": item.get("plan_name") or item.get("name") or label,
            "name": item.get("name") or item.get("plan_name") or label,
            "network": item.get("network"),
            "amount": item.get("amount"),
            "size_gb": item.get("size_gb"),
            "validity_days": item.get("validity_days"),
            "biller_code": item.get("biller_code"),
            "index": item.get("index") or idx,
            "tags": item.get("tags"),
        }
        entities.append(
            ContextEntity(
                entity_type=EntityType.DATA_PLAN,
                entity_id=plan_code or None,
                label=label,
                data={key: value for key, value in data.items() if value is not None and value != ""},
            )
        )
    if not entities:
        return

    frame = ContextFrame(
        frame_id=f"data_plan_{int(time.time())}",
        frame_type=ContextFrameType.DATA_PLAN_LIST,
        items=entities,
        focus_index=0,
        created_at_ts=int(time.time()),
        source_message_id=ctx.state.last_message_id,
        ttl_seconds=900,
    )
    OrchestratorContextManager().push_frame(ctx.state, frame)
    ctx.agg.updates["context_frames"] = ctx.state.context_frames
    ctx.agg.updates["referent_memory"] = ctx.state.referent_memory


def _push_data_plan_frames_from_result(task: Any, result: Any, ctx: ExecutionContext) -> None:
    if task.type != "data" or not isinstance(result.patch, dict):
        return
    if str(task.payload.get("action") or "") == "data_plan_query":
        _push_data_plan_frame(ctx, result.patch.get("data_plan_query_results"))
        return
    _push_data_plan_frame(ctx, result.patch.get("data_plan_candidates"))


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
    confirmation_task_count: int | None = None
    if ctx.state.last_interrupt and task_id in ctx.state.last_interrupt.task_ids:
        raw_required_fields = ctx.state.last_interrupt.fields_by_task.get(task_id, [])
        required_fields = [field for field in raw_required_fields if isinstance(field, str)]
        previous_response = ctx.state.last_interrupt.prompt
        if ctx.state.last_interrupt.kind == "confirmation":
            confirmation_task_count = len(ctx.state.last_interrupt.task_ids)

    user_msg = _maybe_user_message(task, ctx.state)

    awaiting_raw_slot_input = bool(required_fields)
    needs_account_selection = not task.payload.get("source_account_id")
    if (
        (not user_msg or not user_msg.strip())
        and task.payload.get("recipient_name")
        and not needs_account_selection
        and not awaiting_raw_slot_input
    ):
        r_name = task.payload["recipient_name"]
        if isinstance(r_name, str) and r_name.lower() not in ("him", "her", "them", "that", "it", "this", "previous"):
            amt = task.payload.get("amount") or ""
            user_msg = f"Send {amt} to {r_name}"
            logger.info("user_msg_synthesized", msg=user_msg)

    beneficiaries = ctx.state.loaded_context.get("beneficiaries", [])
    if not isinstance(beneficiaries, list):
        beneficiaries = []

    recipient_name = task.payload.get("recipient_name")
    has_recipient_hint = isinstance(recipient_name, str) and bool(recipient_name.strip())
    beneficiary_repo = ctx.config["configurable"].get("beneficiary_repo")
    user_id = ctx.state.loaded_context.get("user_id")
    beneficiary_context_mode = str(ctx.state.loaded_context.get("beneficiary_context_mode") or "full")
    if (
        beneficiary_repo
        and user_id
        and (
            not beneficiaries
            or (
                has_recipient_hint
                and beneficiary_context_mode == "cache_only"
                and _recipient_supports_targeted_beneficiary_lookup(recipient_name)
                and not _beneficiary_cache_contains_recipient(beneficiaries, recipient_name)
            )
        )
    ):
        try:
            fetched_rows: list[Any] = []
            reload_mode = "full"
            if (
                has_recipient_hint
                and beneficiary_context_mode == "cache_only"
                and _recipient_supports_targeted_beneficiary_lookup(recipient_name)
                and hasattr(beneficiary_repo, "search_by_name")
            ):
                fetched_rows = await beneficiary_repo.search_by_name(
                    str(user_id),
                    recipient_name.strip(),
                    beneficiary_type="transfer",
                )
                reload_mode = "targeted"

            if not fetched_rows:
                fetched_rows = await beneficiary_repo.get_by_user(str(user_id), beneficiary_type="transfer")
                if reload_mode == "targeted":
                    reload_mode = "targeted_fallback_full"
                elif beneficiary_context_mode == "cache_only" and not has_recipient_hint:
                    reload_mode = "full_no_recipient_hint"
                else:
                    reload_mode = "full"

            beneficiaries = _normalize_beneficiary_rows(fetched_rows if isinstance(fetched_rows, list) else [])
            if isinstance(ctx.state.loaded_context, dict):
                ctx.state.loaded_context["beneficiaries"] = beneficiaries
            logger.info(
                "transfer_beneficiaries_reloaded_for_resolution",
                user_id=str(user_id),
                fetched_count=len(beneficiaries),
                reload_mode=reload_mode,
            )
        except Exception as e:
            logger.warning(
                "transfer_beneficiary_reload_failed",
                user_id=str(user_id),
                error=str(e),
            )

    resolved_referents = build_resolved_referents(ctx.state, user_msg)
    context_data = {
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "channel_identity": ctx.state.channel_identity,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("transaction_accounts", ctx.state.loaded_context.get("accounts", [])),
        "all_accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": beneficiaries,
        "referent_memory": ctx.state.referent_memory.model_dump(mode="json"),
        "resolved_referents": resolved_referents,
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
        "confirmation_task_count": confirmation_task_count,
        "progress_tracker": ctx.config["configurable"].get("progress_tracker"),
    }
    _stamp_async_group_metadata(task, ctx)
    if task.payload.get("source_affinity_mode") is None:
        task.payload.pop("source_affinity_mode", None)

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
    if isinstance(result.patch, dict):
        schedule_items = result.patch.get("schedule_context_items")
        if isinstance(schedule_items, list):
            _push_schedule_list_frame(ctx, [item for item in schedule_items if isinstance(item, dict)])
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


async def handle_schedule_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    """Run scheduled transaction management through the transfer scheduler worker.

    Schedule management is planner-owned and can be read-only. It must not pass
    through the transfer mandate gate just because the implementation currently
    lives on the transfer worker.
    """

    worker = _get_worker(
        ctx.services,
        "transfer",
        task,
        log_key="schedule_worker_missing",
        error_message=render_message("orchestrator.error.transfer_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    context_data = {
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "channel_identity": ctx.state.channel_identity,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "all_accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
        "language": _state_locale(ctx.state),
        "required_fields": [],
        "previous_response": None,
        "confirmation_task_count": None,
        "progress_tracker": ctx.config["configurable"].get("progress_tracker"),
    }
    user_msg = _maybe_user_message(task, ctx.state)
    logger.info("schedule_worker_start", payload=task.payload, task_id=task_id)
    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
        pin_verified=ctx.state.pin_verified,
    )
    logger.info("schedule_worker_returned", outcome=result.outcome, task_id=task_id)

    _apply_result_patch(task, result)
    if isinstance(result.patch, dict):
        schedule_items = result.patch.get("schedule_context_items")
        if isinstance(schedule_items, list):
            _push_schedule_list_frame(ctx, [item for item in schedule_items if isinstance(item, dict)])
    if result.response:
        ctx.agg.say(result.response)

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
        viewed_accounts = result.details.get("viewed_accounts") if isinstance(result.details, dict) else None
        if isinstance(viewed_accounts, list):
            _push_account_list_frame(ctx, [item for item in viewed_accounts if isinstance(item, dict)])
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
    del task_id
    action = task.payload.get("action")
    is_management = (
        task.payload.get("intent")
        or task.payload.get("list_intent")
        or action in ("list_beneficiaries", "add_beneficiary", "delete_beneficiary", "update_beneficiary")
    )

    if is_management:
        from apps.chat.src.agent.graphs.beneficiary.worker import BeneficiaryWorker

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

            if result.details and "viewed_beneficiaries" in result.details:
                viewed = result.details["viewed_beneficiaries"]
                if viewed:
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

                    ctx_manager.push_frame(ctx.state, frame)
                    ctx.agg.updates["context_frames"] = ctx.state.context_frames
                    ctx.agg.updates["referent_memory"] = ctx.state.referent_memory
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
        "accounts": ctx.state.loaded_context.get("transaction_accounts", ctx.state.loaded_context.get("accounts", [])),
        "all_accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
        "referent_memory": ctx.state.referent_memory.model_dump(mode="json"),
        "resolved_referents": build_resolved_referents(ctx.state, user_msg),
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
    }
    if include_channel:
        context_data["channel"] = ctx.state.channel
        context_data["channel_identity"] = ctx.state.channel_identity
    _stamp_async_group_metadata(task, ctx)

    result = await worker.run(
        payload=task.payload,
        context=context_data,
        user_message=user_msg,
        pin_verified=ctx.state.pin_verified,
    )

    _apply_result_patch(task, result)

    if worker_name == "data" and str(task.payload.get("action") or "") == "data_plan_query":
        task.payload["skip_finalize_summary"] = True
        if result.outcome == TransactionOutcome.OK and result.response:
            ctx.agg.say(result.response)

    if worker_name == "data":
        _push_data_plan_frames_from_result(task, result, ctx)

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
        "inbound_message_id": ctx.state.last_message_id,
        "turn_id": ctx.state.last_message_id,
        "stashed_query_session": ctx.state.stashed_query_session,
        "progress_tracker": ctx.config["configurable"].get("progress_tracker"),
    }

    result = await worker.run(
        payload=task.payload,
        context=context_data,
    )

    _apply_result_patch(task, result)
    if ctx.state.stashed_query_session is not None:
        ctx.agg.updates["stashed_query_session"] = None
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
            pagination_payload = _query_pagination_actionable_payload(ctx, result)
            if pagination_payload:
                ctx.agg.add_outbox(
                    {
                        "type": "say",
                        "text": result.response,
                        "actionable_payload": pagination_payload,
                    }
                )
            else:
                ctx.agg.say(result.response)

        if result.patch and isinstance(result.patch, dict):
            _push_query_surface_frame(ctx, result.patch.get("query_result"))

        if followup_referent and not handoff_payload:
            _push_query_followup_referent_frame(ctx, followup_referent)

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
        include_channel=True,
    )


async def handle_faq_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    del task_id
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
    stash_id = str(last_session.get("stash_id") or "").strip()
    if stash_id:
        forget_stashed_referents(ctx.state, {stash_id})
        ctx.agg.updates["referent_memory"] = ctx.state.referent_memory

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
