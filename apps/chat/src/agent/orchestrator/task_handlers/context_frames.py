"""Context-frame helpers for orchestrator execution handlers."""

import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.surface_adapter import build_context_frame_from_surface_view
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.execution.runtime import ExecutionTurnContext
from banking.transactions.query.contracts import FocusedReferent, SelectionPayload
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def is_resume_prompt_frame(frame: ContextFrame) -> bool:
    """Return True if a context frame represents a resume prompt."""
    return any(item.data.get("resume_prompt") is True for item in frame.items)


def clear_resume_prompt_frames(frames: list[ContextFrame]) -> list[ContextFrame]:
    """Remove stale resume prompt frames after accept/decline."""
    return [frame for frame in frames if not is_resume_prompt_frame(frame)]


def _push_frame(ctx: ExecutionTurnContext, frame: ContextFrame) -> None:
    OrchestratorContextManager().push_frame(ctx.state, frame)
    ctx.accumulator.set_update("context_frames", ctx.state.context_frames)
    ctx.accumulator.set_update("referent_memory", ctx.state.referent_memory)


def push_query_followup_referent_frame(
    ctx: ExecutionTurnContext,
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
    _push_frame(ctx, frame)


def push_account_list_frame(ctx: ExecutionTurnContext, accounts: list[dict[str, Any]]) -> None:
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
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="account_list", count=len(entities))


def push_schedule_list_frame(ctx: ExecutionTurnContext, items: list[dict[str, Any]]) -> None:
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
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="schedule_list", count=len(entities))


def push_query_surface_frame(ctx: ExecutionTurnContext, query_result: Any) -> None:
    if query_result is None:
        return

    surface_view = getattr(query_result, "surface_view", None)
    if surface_view is None:
        try:
            from banking.transactions.query.presentation.surface_builder import build_surface_view

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

    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type=frame.frame_type.value, count=len(frame.items))


def query_pagination_actionable_payload(ctx: ExecutionTurnContext, result: Any) -> dict[str, Any] | None:
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


def push_data_plan_frame(ctx: ExecutionTurnContext, results: Any) -> None:
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
    _push_frame(ctx, frame)


def push_data_plan_frames_from_result(task: TaskSpec, result: Any, ctx: ExecutionTurnContext) -> None:
    if task.type != "data" or not isinstance(result.patch, dict):
        return
    if str(task.payload.get("action") or "") == "data_plan_query":
        push_data_plan_frame(ctx, result.patch.get("data_plan_query_results"))
        return
    push_data_plan_frame(ctx, result.patch.get("data_plan_candidates"))


def push_beneficiary_list_frame(ctx: ExecutionTurnContext, viewed_beneficiaries: Any) -> None:
    if not viewed_beneficiaries:
        return

    entities: list[ContextEntity] = []
    for beneficiary in viewed_beneficiaries:
        if not isinstance(beneficiary, dict):
            continue
        label = str(beneficiary.get("alias") or beneficiary.get("name") or "").strip()
        if not label:
            continue
        entities.append(ContextEntity(entity_type=EntityType.BENEFICIARY, label=label, data=beneficiary))
    if not entities:
        return

    frame = ContextFrame(
        frame_id=f"frame_{int(time.time())}",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=entities,
        created_at_ts=int(time.time()),
        source_message_id=ctx.state.last_message_id,
    )
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="beneficiary_list", count=len(entities))
