from __future__ import annotations

import time

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager


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

    entity = OrchestratorContextManager().resolve_reference(state, {"selector": "previous"})

    assert entity is not None
    assert entity.label == "Mum"
