"""Single-entity detail block rendering for context-frame answers."""

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_data_plans import (
    format_data_plan_detail_block,
    is_data_plan_entity,
    is_data_plan_frame,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_fields import (
    candidate_detail_fields,
)
from banking.presentation.i18n.renderer import render_message


def format_detail_block(entity: ContextEntity, *, ordinal: int | None = None, locale: str = "en") -> str | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    if is_data_plan_entity(entity):
        return format_data_plan_detail_block(entity, ordinal=ordinal)
    if data.get("type") == "scheduled_transaction":
        header = entity.label or render_message("context_frame.noun.scheduled_transaction", locale)
        if ordinal is not None:
            header = f"{ordinal}. {header}"
        lines = [header]
        next_run = data.get("next_run")
        source_bank = data.get("source_bank_name")
        status = data.get("status")
        if next_run:
            lines.append(render_message("context_frame.detail.next_run", locale, {"value": str(next_run)}))
        if source_bank:
            lines.append(render_message("context_frame.detail.from", locale, {"value": str(source_bank)}))
        if status:
            lines.append(render_message("context_frame.detail.status", locale, {"value": str(status)}))
        return "\n".join(lines) if len(lines) > 1 else header

    header = entity.label or render_message("context_frame.noun.item", locale)
    if ordinal is not None:
        header = f"{ordinal}. {header}"

    lines = [header]
    for label, value in candidate_detail_fields(entity):
        lines.append(f"{label}: {value}")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def detail_header(frame: ContextFrame, *, locale: str = "en") -> str:
    if is_data_plan_frame(frame):
        return render_message("context_frame.header.data_plan_details", locale)
    if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
        if len(frame.items) > 1:
            return render_message("context_frame.header.saved_beneficiary_details", locale)
        return render_message("context_frame.header.beneficiary_details", locale)
    if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
        if len(frame.items) > 1:
            return render_message("context_frame.header.linked_account_details", locale)
        return render_message("context_frame.header.account_details", locale)
    if frame.frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.TRANSACTION_DETAIL}:
        return render_message("context_frame.header.transaction_details", locale)
    if frame.frame_type == ContextFrameType.RECEIPT:
        return render_message("context_frame.header.receipt_details", locale)
    if frame.frame_type == ContextFrameType.SCHEDULE_LIST:
        return render_message("context_frame.header.scheduled_transaction_details", locale)
    return render_message("context_frame.header.details", locale)


__all__ = ["detail_header", "format_detail_block"]
