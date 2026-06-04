"""Context-frame persistence for planner context-read responses."""

import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_availability import (
    _has_context_for_read_subtype,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import (
    BENEFICIARY_CONTEXT_READ_PERSIST_SUBTYPES,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_beneficiary_context_read_updates(
    state_view: PlannerStateView,
    planner_output: Any,
    subtype: str | None,
) -> dict[str, Any]:
    """Persist beneficiary context-read entities as context frames for pronoun follow-ups."""
    if subtype not in BENEFICIARY_CONTEXT_READ_PERSIST_SUBTYPES:
        return {}
    if (
        not planner_output
        or planner_output.primary_intent != "conversational"
        or getattr(planner_output, "tasks", None)
    ):
        return {}
    if not _has_context_for_read_subtype(state_view, subtype):
        return {}

    raw_beneficiaries = state_view.loaded_context_or_empty.get("beneficiaries")
    if not isinstance(raw_beneficiaries, list):
        return {}

    entities: list[ContextEntity] = []
    for item in raw_beneficiaries:
        if not isinstance(item, dict):
            continue
        label = str(item.get("alias") or item.get("account_name") or item.get("name") or "Beneficiary").strip()
        beneficiary_id = item.get("id")
        entity_id = str(beneficiary_id).strip() if beneficiary_id is not None else None
        data = {
            "id": entity_id,
            "alias": item.get("alias"),
            "account_name": item.get("account_name"),
            "account_number": item.get("account_number"),
            "bank_name": item.get("bank_name"),
            "bank_code": item.get("bank_code"),
            "beneficiary_type": item.get("beneficiary_type"),
        }
        entities.append(ContextEntity(entity_type=EntityType.BENEFICIARY, entity_id=entity_id, label=label, data=data))

    if not entities:
        return {}

    frame = ContextFrame(
        frame_id=f"planner_beneficiaries_{int(time.time())}",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=entities,
        created_at_ts=int(time.time()),
        source_message_id=state_view.last_message_id,
    )
    OrchestratorContextManager().push_frame(state_view.state, frame)
    logger.info("planner_context_read_frame_pushed", subtype=subtype, count=len(entities))
    return {"context_frames": state_view.context_frames, "referent_memory": state_view.referent_memory}


__all__ = ["_build_beneficiary_context_read_updates"]
