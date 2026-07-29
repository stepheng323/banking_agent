"""Context-frame helpers for orchestrator execution handlers."""

import time
from hashlib import sha256
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.surface_adapter import build_context_frame_from_surface_view
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import (
    context_surface,
    push_context_frame,
    replace_context_frames,
)
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from banking.transactions.query.contracts import FocusedReferent, SelectionPayload
from banking.transactions.query.grounding.frames import build_query_frame
from shared.types.conversation_sets import ConversationSetState, EntitySelectionRef
from shared.types.read import ReadResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def is_resume_prompt_frame(frame: ContextFrame) -> bool:
    """Return True if a context frame represents a resume prompt."""
    return any(item.data.get("resume_prompt") is True for item in frame.items)


def clear_resume_prompt_frames(frames: list[ContextFrame]) -> list[ContextFrame]:
    """Remove stale resume prompt frames after accept/decline."""
    return [frame for frame in frames if not is_resume_prompt_frame(frame)]


def invalidate_conversation_set_frames(ctx: ExecutionTurnContext, domain: str) -> None:
    """Remove stale set frames after a successful mutation."""
    frame_type = {
        "beneficiary": ContextFrameType.BENEFICIARY_LIST,
        "schedule": ContextFrameType.SCHEDULE_LIST,
        "linked_account": ContextFrameType.ACCOUNT_LIST,
    }.get(domain)
    if frame_type is None:
        return
    retained = [frame for frame in context_surface(ctx.state).frames if frame.frame_type != frame_type]
    replace_context_frames(ctx, retained)
    logger.info("conversation_set_frames_invalidated", domain=domain)


def _push_frame(ctx: ExecutionTurnContext, frame: ContextFrame) -> None:
    push_context_frame(ctx, frame)


def _replace_prior_read_frame(ctx: ExecutionTurnContext, *, subject: str | None = None) -> None:
    frames = [
        frame
        for frame in context_surface(ctx.state).frames
        if not (
            isinstance(frame.metadata.get("read_request"), dict)
            and (subject is None or cast(dict[str, Any], frame.metadata["read_request"]).get("subject") == subject)
        )
    ]
    replace_context_frames(ctx, frames)


def _next_conversation_set_state(
    *,
    domain: str,
    refs: list[EntitySelectionRef],
    prior: Any,
    active_filters: dict[str, str] | None = None,
) -> ConversationSetState:
    try:
        previous = ConversationSetState.model_validate(prior) if isinstance(prior, dict) else None
    except ValueError:
        previous = None
    mentioned = list(previous.mentioned_refs) if previous is not None and previous.domain == domain else []
    by_id = {ref.entity_id: ref for ref in mentioned}
    for ref in refs:
        by_id[ref.entity_id] = ref
    return ConversationSetState(
        domain=domain,  # type: ignore[arg-type]
        focused_ref=refs[0] if len(refs) == 1 else None,
        mentioned_refs=list(by_id.values())[-20:],
        last_result_refs=refs,
        active_filters=active_filters or (previous.active_filters if previous is not None else {}),
        operation=(previous.operation if previous is not None else None),
        created_turn_id=(previous.created_turn_id if previous is not None else None),
        updated_turn_id=(previous.updated_turn_id if previous is not None else None),
    )


def _contract_filter_summary(metadata: dict[str, Any], key: str) -> dict[str, str]:
    raw = metadata.get(key)
    if not isinstance(raw, dict):
        return {}
    ignored = {"operation", "response_shape", "scope"}
    filters: dict[str, str] = {}
    for name, value in raw.items():
        if name in ignored or value in (None, "", [], "any"):
            continue
        filters[name] = ",".join(str(item) for item in value) if isinstance(value, list) else str(value)
    return filters


def push_read_result_frame(
    ctx: ExecutionTurnContext,
    read_result: ReadResult,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist fact-only read context without storing undisplayed result rows."""
    request = read_result.request
    entity = ContextEntity(
        entity_type=EntityType.GENERIC,
        entity_id=f"read_{request.subject}_{int(time.time())}",
        label=f"{request.subject} {request.response_shape} ({read_result.total_count})",
        data={"read_summary": True},
    )
    frame = ContextFrame(
        frame_id=f"read_surface_{int(time.time())}",
        frame_type=ContextFrameType.GENERIC,
        items=[entity],
        focus_index=0,
        created_at_ts=int(time.time()),
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata={
            "source": request.subject,
            "read_request": request.model_dump(mode="json", exclude_none=True),
            "total_count": read_result.total_count,
            "has_next": read_result.has_next,
            "has_previous": read_result.has_previous,
            **(metadata or {}),
        },
    )
    _replace_prior_read_frame(ctx, subject=request.subject)
    _push_frame(ctx, frame)
    logger.info(
        "canonical_read_context_persisted",
        subject=request.subject,
        response_shape=request.response_shape,
        filter_types=[
            key
            for key, value in (
                ("entity_name", request.entity_name),
                ("bank_name", request.bank_name),
                ("status", request.status),
                ("reference", request.reference),
            )
            if value
        ],
        total_count=read_result.total_count,
        returned_count=read_result.returned_count,
        has_next=read_result.has_next,
        has_previous=read_result.has_previous,
    )


def push_query_followup_referent_frame(
    ctx: ExecutionTurnContext,
    referent: FocusedReferent | dict[str, Any],
) -> None:
    def _get(key: str, default: Any = None) -> Any:
        if isinstance(referent, dict):
            return referent.get(key, default)
        return getattr(referent, key, default)

    label = str(_get("label") or _get("recipient_name") or "").strip()
    if not label:
        return

    entity_id = str(_get("entity_id") or "").strip() or None
    recipient_name = str(_get("recipient_name") or label).strip()
    account_number = str(_get("recipient_account") or "").strip() or None
    bank_name = str(_get("recipient_bank_name") or "").strip() or None
    bank_code = str(_get("recipient_bank_code") or "").strip() or None
    resolved_name = str(_get("recipient_resolved_name") or recipient_name).strip()
    selection_payload = _get("selection_payload") if isinstance(referent, FocusedReferent) else None
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
        source_message_id=turn_metadata(ctx.state).last_message_id,
    )
    _push_frame(ctx, frame)


def push_account_list_frame(
    ctx: ExecutionTurnContext,
    accounts: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not accounts:
        return

    now = int(time.time())
    frame_id = f"account_list_{now}"
    read_request = (metadata or {}).get("read_request")
    is_lifecycle = isinstance(read_request, dict) and read_request.get("subject") in {
        "linked_account",
        "default_account",
    }
    entities: list[ContextEntity] = []
    selection_refs: list[EntitySelectionRef] = []
    for account in accounts:
        bank_name = str(account.get("bank_name") or "Account").strip()
        account_number = str(account.get("account_number") or "").strip()
        last4 = str(account.get("account_number_last4") or account.get("last4") or "").strip()
        if not last4:
            last4 = account_number[-4:] if account_number else "????"
        label = f"{bank_name} (...{last4})"
        entity_id = str(account.get("account_id") or account.get("id") or "").strip() or None
        version_token = str(account.get("version_token") or "").strip() or None
        safe_data = account
        if is_lifecycle:
            safe_data = {
                "account_id": entity_id,
                "bank_name": bank_name,
                "account_number_last4": last4,
                "mandate_status": account.get("mandate_status"),
                "is_default": account.get("is_default"),
                "version_token": version_token,
            }
        entities.append(
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id=entity_id,
                label=label,
                data={key: value for key, value in safe_data.items() if value is not None},
            )
        )
        if is_lifecycle and entity_id and version_token:
            selection_refs.append(
                EntitySelectionRef(
                    entity_type="linked_account",
                    entity_id=entity_id,
                    frame_id=frame_id,
                    display_label=label,
                    version_token=version_token,
                )
            )

    frame_metadata = dict(metadata or {})
    if selection_refs:
        frame_metadata["conversation_set_state"] = _next_conversation_set_state(
            domain="linked_account",
            refs=selection_refs,
            prior=frame_metadata.get("conversation_set_state"),
            active_filters=_contract_filter_summary(frame_metadata, "account_lifecycle_contract"),
        ).model_dump(mode="json", exclude_none=True)
    frame = ContextFrame(
        frame_id=frame_id,
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=entities,
        focus_index=0,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata=frame_metadata,
    )
    if isinstance((metadata or {}).get("read_request"), dict):
        request = cast(dict[str, Any], (metadata or {})["read_request"])
        _replace_prior_read_frame(ctx, subject=str(request.get("subject") or "linked_account"))
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="account_list", count=len(entities))


def push_schedule_list_frame(
    ctx: ExecutionTurnContext,
    items: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not items:
        return

    now = int(time.time())
    frame_id = f"schedule_list_{now}"
    entities: list[ContextEntity] = []
    selection_refs: list[EntitySelectionRef] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            data = {}
        label = str(item.get("label") or data.get("summary") or f"Scheduled transaction {idx}").strip()
        entity_id = str(item.get("entity_id") or data.get("schedule_id") or "").strip() or None
        version_token = str(data.get("version_token") or "").strip() or None
        entities.append(
            ContextEntity(
                entity_type=EntityType.SCHEDULE,
                entity_id=entity_id,
                label=label,
                data=data,
            )
        )
        if entity_id and version_token:
            selection_refs.append(
                EntitySelectionRef(
                    entity_type="schedule",
                    entity_id=entity_id,
                    frame_id=frame_id,
                    display_label=label,
                    version_token=version_token,
                )
            )

    if not entities:
        return

    frame_metadata = dict(metadata or {})
    if selection_refs:
        frame_metadata["conversation_set_state"] = _next_conversation_set_state(
            domain="schedule",
            refs=selection_refs,
            prior=frame_metadata.get("conversation_set_state"),
            active_filters=_contract_filter_summary(frame_metadata, "schedule_contract"),
        ).model_dump(mode="json", exclude_none=True)
    frame = ContextFrame(
        frame_id=frame_id,
        frame_type=ContextFrameType.SCHEDULE_LIST,
        items=entities,
        focus_index=0,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata=frame_metadata,
    )
    if isinstance((metadata or {}).get("read_request"), dict):
        request = cast(dict[str, Any], (metadata or {})["read_request"])
        _replace_prior_read_frame(ctx, subject=str(request.get("subject") or "schedule"))
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="schedule_list", count=len(entities))


def push_schedule_run_list_frame(
    ctx: ExecutionTurnContext,
    items: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist displayed run references as a dedicated read-only frame."""
    if not items:
        return
    now = int(time.time())
    entities = [
        ContextEntity(
            entity_type=EntityType.SCHEDULE_RUN,
            entity_id=str(item.get("id") or "") or None,
            label=str(item.get("display_label") or "").strip(),
            data={"version_token": item.get("version_token"), "read_only": True},
        )
        for item in items
        if isinstance(item, dict) and str(item.get("display_label") or "").strip()
    ]
    if not entities:
        return
    frame = ContextFrame(
        frame_id=f"schedule_run_list_{now}",
        frame_type=ContextFrameType.SCHEDULE_RUN_LIST,
        items=entities,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata={**(metadata or {}), "read_only": True},
    )
    _replace_prior_read_frame(ctx, subject="schedule")
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="schedule_run_list", count=len(entities))


def push_query_surface_frame(ctx: ExecutionTurnContext, query_result: Any) -> None:
    if query_result is None or isinstance(query_result, dict):
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
        frame = _build_direct_query_summary_frame(ctx, query_result, surface_view=surface_view)
        if frame is None:
            return
        _push_frame(ctx, frame)
        logger.info("context_frame_pushed", type=frame.frame_type.value, count=len(frame.items))
        return

    frame = build_context_frame_from_surface_view(
        surface_view,
        source="query",
        source_message_id=turn_metadata(ctx.state).last_message_id,
    )
    if frame is None:
        return
    query_request = getattr(query_result, "query_request", None)
    if query_request is not None and hasattr(query_request, "model_dump"):
        try:
            frame.metadata["query_frame"] = build_query_frame(
                query_request=query_request,
                result=query_result,
                turn_index=context_surface(ctx.state).frame_count + 1,
                focus=getattr(query_result, "conversation_focus", None),
            ).model_dump(mode="json")
        except Exception as exc:
            logger.warning("query_context_frame_compact_frame_failed", error=str(exc))
    summary_text = getattr(query_result, "summary_text", None)
    if isinstance(summary_text, str) and summary_text.strip():
        frame.metadata["summary_text"] = summary_text.strip()
    if surface_view.lead_text:
        frame.metadata["lead_text"] = surface_view.lead_text
    frame.metadata["surface_context"] = surface_view.context
    frame.metadata["has_more"] = bool(getattr(query_result, "has_more", False))

    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type=frame.frame_type.value, count=len(frame.items))


def _build_direct_query_summary_frame(
    ctx: ExecutionTurnContext,
    query_result: Any,
    *,
    surface_view: Any | None,
) -> ContextFrame | None:
    """Build a compact query frame for direct answers that have no visible rows."""
    query_request = getattr(query_result, "query_request", None)
    if query_request is None or not hasattr(query_request, "model_dump"):
        return None

    summary_text = str(getattr(query_result, "summary_text", "") or "").strip()
    lead_text = str(getattr(surface_view, "lead_text", "") or "").strip() if surface_view is not None else ""
    label = summary_text or lead_text
    if not label:
        return None

    now = int(time.time())
    entity = ContextEntity(
        entity_type=EntityType.GENERIC,
        entity_id=f"query_summary_{now}",
        label=label,
        data={
            "id": f"query_summary_{now}",
            "label": label,
            "surface_mode": "direct_answer",
            "lead_text": lead_text or summary_text,
        },
    )
    frame = ContextFrame(
        frame_id=f"query_surface_{now}",
        frame_type=ContextFrameType.GENERIC,
        items=[entity],
        focus_index=0,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata={
            "source": "query",
            "surface_mode": "direct_answer",
            "summary_text": summary_text,
            "lead_text": lead_text or None,
            "surface_context": getattr(surface_view, "context", {}) if surface_view is not None else {},
            "has_more": bool(getattr(query_result, "has_more", False)),
        },
    )
    try:
        frame.metadata["query_frame"] = build_query_frame(
            query_request=query_request,
            result=query_result,
            turn_index=context_surface(ctx.state).frame_count + 1,
            focus=getattr(query_result, "conversation_focus", None),
        ).model_dump(mode="json")
    except Exception as exc:
        logger.warning("query_context_frame_compact_frame_failed", error=str(exc))
    return frame


def query_pagination_actionable_payload(ctx: ExecutionTurnContext, result: Any) -> dict[str, Any] | None:
    if not turn_metadata(ctx.state).is_telegram or not isinstance(result.patch, dict):
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
    now = int(time.time())
    frame_id = f"data_plan_{now}"
    entities: list[ContextEntity] = []
    selection_refs: list[EntitySelectionRef] = []
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
        if plan_code:
            version_source = ":".join(
                str(value or "")
                for value in (
                    plan_code,
                    data.get("network"),
                    data.get("amount"),
                    data.get("size_gb"),
                    data.get("validity_days"),
                )
            )
            selection_refs.append(
                EntitySelectionRef(
                    entity_type="data_plan",
                    entity_id=plan_code,
                    frame_id=frame_id,
                    display_label=label,
                    version_token=sha256(version_source.encode()).hexdigest()[:20],
                )
            )
    if not entities:
        return

    metadata: dict[str, Any] = {}
    if selection_refs:
        metadata["conversation_set_state"] = _next_conversation_set_state(
            domain="data_plan",
            refs=selection_refs,
            prior=None,
        ).model_dump(mode="json", exclude_none=True)
    frame = ContextFrame(
        frame_id=frame_id,
        frame_type=ContextFrameType.DATA_PLAN_LIST,
        items=entities,
        focus_index=0,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        ttl_seconds=600,
        metadata=metadata,
    )
    _push_frame(ctx, frame)


def push_data_plan_frames_from_result(task: TaskSpec, result: Any, ctx: ExecutionTurnContext) -> None:
    if task.type != "data" or not isinstance(result.patch, dict):
        return
    if str(task.payload.get("action") or "") == "data_plan_query":
        push_data_plan_frame(ctx, result.patch.get("data_plan_query_results"))
        return
    push_data_plan_frame(ctx, result.patch.get("data_plan_candidates"))


def push_beneficiary_list_frame(
    ctx: ExecutionTurnContext,
    viewed_beneficiaries: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not viewed_beneficiaries:
        return

    now = int(time.time())
    frame_id = f"beneficiary_list_{now}"
    entities: list[ContextEntity] = []
    selection_refs: list[EntitySelectionRef] = []
    for beneficiary in viewed_beneficiaries:
        if not isinstance(beneficiary, dict):
            continue
        label = str(beneficiary.get("alias") or beneficiary.get("name") or "").strip()
        if not label:
            continue
        bank_name = str(beneficiary.get("bank") or beneficiary.get("bank_name") or "").strip()
        account_number = str(beneficiary.get("account") or beneficiary.get("account_number") or "").strip()
        last4 = account_number[-4:] if account_number else ""
        display_label = label
        if bank_name:
            display_label = f"{display_label} · {bank_name}"
        if last4:
            display_label = f"{display_label} · ···{last4}"
        entity_id = str(beneficiary.get("id") or "").strip() or None
        version_token = str(beneficiary.get("version_token") or "").strip() or None
        safe_data = {
            "id": entity_id,
            "version_token": version_token,
            "bank": bank_name or None,
            "account_last4": last4 or None,
            "beneficiary_type": beneficiary.get("beneficiary_type"),
        }
        entities.append(
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id=entity_id,
                label=display_label,
                data={key: value for key, value in safe_data.items() if value is not None},
            )
        )
        if entity_id and version_token:
            selection_refs.append(
                EntitySelectionRef(
                    entity_type="beneficiary",
                    entity_id=entity_id,
                    frame_id=frame_id,
                    display_label=display_label,
                    version_token=version_token,
                )
            )
    if not entities:
        return

    frame_metadata = dict(metadata or {})
    if selection_refs:
        frame_metadata["conversation_set_state"] = _next_conversation_set_state(
            domain="beneficiary",
            refs=selection_refs,
            prior=frame_metadata.get("conversation_set_state"),
            active_filters=_contract_filter_summary(frame_metadata, "beneficiary_contract"),
        ).model_dump(mode="json", exclude_none=True)
    frame = ContextFrame(
        frame_id=frame_id,
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=entities,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata=frame_metadata,
    )
    if isinstance((metadata or {}).get("read_request"), dict):
        _replace_prior_read_frame(ctx, subject="beneficiary")
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="beneficiary_list", count=len(entities))


def push_support_ticket_list_frame(
    ctx: ExecutionTurnContext,
    viewed_tickets: Any,
    *,
    read_result: ReadResult,
) -> None:
    """Persist only the ticket references actually shown to the user."""
    now = int(time.time())
    entities: list[ContextEntity] = []
    for item in viewed_tickets:
        if not isinstance(item, dict):
            continue
        ticket_id = str(item.get("id") or "").strip()
        label = str(item.get("display_label") or item.get("code") or "").strip()
        if not ticket_id or not label:
            continue
        entities.append(
            ContextEntity(
                entity_type=EntityType.SUPPORT_TICKET,
                entity_id=ticket_id,
                label=label,
                data={
                    "ticket_code": item.get("code"),
                    "status": item.get("status"),
                    "summary": item.get("summary"),
                    "priority": item.get("priority"),
                    "version_token": item.get("version_token"),
                },
            )
        )
    if not entities:
        push_read_result_frame(ctx, read_result)
        return
    frame = ContextFrame(
        frame_id=f"support_ticket_list_{now}",
        frame_type=ContextFrameType.SUPPORT_TICKET_LIST,
        items=entities,
        created_at_ts=now,
        source_message_id=turn_metadata(ctx.state).last_message_id,
        metadata={
            "read_request": read_result.request.model_dump(mode="json", exclude_none=True),
            "total_count": read_result.total_count,
            "has_next": read_result.has_next,
            "has_previous": read_result.has_previous,
        },
    )
    _replace_prior_read_frame(ctx, subject="ticket")
    _push_frame(ctx, frame)
    logger.info("context_frame_pushed", type="support_ticket_list", count=len(entities))
