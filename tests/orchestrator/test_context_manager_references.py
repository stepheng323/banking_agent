from __future__ import annotations

import time

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def test_previous_reference_uses_focused_item_before_last_item() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_ctx_ref",
        phone_number="2348000000999",
        channel="whatsapp",
        context_frames=[
            ContextFrame(
                frame_id="bene_focus",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Mum",
                        data={"alias": "Mum"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Dad",
                        data={"alias": "Dad"},
                    ),
                ],
                focus_index=0,
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )

    entity = ContextFrameManager().resolve_reference(state, {"selector": "previous"})

    assert entity is not None
    assert entity.label == "Mum"
