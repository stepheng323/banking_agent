import time

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.surface_adapter import build_context_frame_from_surface_view
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from apps.chat.src.agent.shared.query_contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from shared.types.planner import ContextFrameFollowupDecision


def test_surface_adapter_builds_transaction_list_frame_and_filters_sensitive_data() -> None:
    surface = SurfaceView(
        mode=SurfaceViewMode.TRANSACTION_LIST,
        items=[
            SurfaceItemView(
                id="tx-1",
                label="Credit from Ada",
                amount=5000.0,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-1",
                    label="Credit from Ada",
                ),
                metadata={
                    "bank_name": "GTBank",
                    "transaction_type": "credit",
                    "status": "successful",
                    "auth_token": "should-not-leak",
                },
            )
        ],
        context={"type": "transaction_list", "pin_token": "hidden"},
    )

    frame = build_context_frame_from_surface_view(surface, source="query", source_message_id="msg-1")

    assert frame is not None
    assert frame.frame_type == ContextFrameType.TRANSACTION_LIST
    assert frame.source_message_id == "msg-1"
    assert frame.items[0].entity_type == EntityType.TRANSACTION
    assert frame.items[0].selection_payload is not None
    assert frame.items[0].data["bank_name"] == "GTBank"
    assert frame.items[0].data["transaction_type"] == "credit"
    assert "auth_token" not in frame.items[0].data
    assert "pin_token" not in frame.items[0].data


def test_surface_adapter_builds_transaction_detail_with_focused_referent() -> None:
    surface = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="tx-2",
                label="Transfer to Tolu",
                amount=2000.0,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-2",
                    label="Transfer to Tolu",
                    handoff_payload={
                        "recipient_name": "Tolu",
                        "recipient_account": "2010000001",
                        "recipient_bank_name": "Access Bank",
                    },
                ),
                metadata={"date": "2026-05-01", "bank_name": "Access Bank", "status": "successful"},
            )
        ],
    )

    frame = build_context_frame_from_surface_view(surface, source="query")

    assert frame is not None
    assert frame.frame_type == ContextFrameType.TRANSACTION_DETAIL
    assert frame.items[0].focused_referent is not None
    assert frame.items[0].focused_referent.recipient_name == "Tolu"
    assert frame.items[0].focused_referent.recipient_bank_name == "Access Bank"


def test_surface_answer_filters_transaction_frame_by_visible_metadata() -> None:
    surface = SurfaceView(
        mode=SurfaceViewMode.TRANSACTION_LIST,
        items=[
            SurfaceItemView(
                id="tx-1",
                label="Credit from Ada",
                amount=5000.0,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-1",
                    label="Credit from Ada",
                ),
                metadata={"bank_name": "GTBank", "transaction_type": "credit", "status": "successful"},
            ),
            SurfaceItemView(
                id="tx-2",
                label="Transfer to Tolu",
                amount=2000.0,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-2",
                    label="Transfer to Tolu",
                ),
                metadata={"bank_name": "Access Bank", "transaction_type": "debit", "status": "successful"},
            ),
        ],
    )
    frame = build_context_frame_from_surface_view(surface, source="query")
    assert frame is not None
    state = OrchestratorState(
        user_id="u-surface",
        phone_number="2348000000000",
        channel="telegram",
        last_message_text="what about credits?",
        context_frames=[frame.model_copy(update={"created_at_ts": int(time.time())})],
    )

    response = build_context_frame_followup_response(
        state,
        "what about credits?",
        decision=ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.92,
            target_text="credit",
        ),
    )

    assert response is not None
    assert "Credit from Ada" in response.response
    assert "Transfer to Tolu" not in response.response
    assert "GTBank" in response.response
