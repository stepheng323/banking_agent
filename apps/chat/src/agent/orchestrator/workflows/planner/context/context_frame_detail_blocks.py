"""Single-entity detail block rendering for context-frame answers."""

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_data_plans import (
    format_data_plan_detail_block,
    is_data_plan_entity,
    is_data_plan_frame,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_detail_fields import (
    candidate_detail_fields,
)


def format_detail_block(entity: ContextEntity, *, ordinal: int | None = None) -> str | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    if is_data_plan_entity(entity):
        return format_data_plan_detail_block(entity, ordinal=ordinal)
    if data.get("type") == "scheduled_transaction":
        header = entity.label or "Scheduled transaction"
        if ordinal is not None:
            header = f"{ordinal}. {header}"
        lines = [header]
        next_run = data.get("next_run")
        source_bank = data.get("source_bank_name")
        status = data.get("status")
        if next_run:
            lines.append(f"Next run: {next_run}")
        if source_bank:
            lines.append(f"From: {source_bank}")
        if status:
            lines.append(f"Status: {status}")
        return "\n".join(lines) if len(lines) > 1 else header

    header = entity.label or "Item"
    if ordinal is not None:
        header = f"{ordinal}. {header}"

    lines = [header]
    for label, value in candidate_detail_fields(entity):
        lines.append(f"{label}: {value}")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def detail_header(frame: ContextFrame) -> str:
    if is_data_plan_frame(frame):
        return "Data Plan Details"
    if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "Saved Beneficiary Details" if len(frame.items) > 1 else "Beneficiary Details"
    if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
        return "Linked Account Details" if len(frame.items) > 1 else "Account Details"
    if frame.frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.TRANSACTION_DETAIL}:
        return "Transaction Details"
    if frame.frame_type == ContextFrameType.RECEIPT:
        return "Receipt Details"
    if frame.frame_type == ContextFrameType.SCHEDULE_LIST:
        return "Scheduled Transaction Details"
    return "Details"


__all__ = ["detail_header", "format_detail_block"]
