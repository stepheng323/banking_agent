"""Tests for callback turn handling in ingest node."""

import pytest

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.ingest import ingest_message


@pytest.mark.asyncio
async def test_callback_turn_clears_stale_text_and_sets_pin_verified() -> None:
    state = OrchestratorState(
        user_id="u_ingest_1",
        phone_number="2348000000001",
        channel="whatsapp",
        last_message_text="yes",
        last_message_id="msg-1",
        last_callback={"pin_verified": True, "flow_type": "transfer"},
    )

    updates = await ingest_message(state)

    assert updates["pin_verified"] is True
    assert updates["last_message_text"] is None
    assert updates["last_message_id"] is None


@pytest.mark.asyncio
async def test_callback_turn_clears_stale_text_even_without_pin_verified() -> None:
    state = OrchestratorState(
        user_id="u_ingest_2",
        phone_number="2348000000002",
        channel="whatsapp",
        last_message_text="ignore this",
        last_message_id="msg-2",
        last_callback={"flow_type": "transfer"},
    )

    updates = await ingest_message(state)

    assert "pin_verified" not in updates
    assert updates["last_message_text"] is None
    assert updates["last_message_id"] is None


@pytest.mark.asyncio
async def test_non_callback_turn_does_not_clear_text_fields() -> None:
    state = OrchestratorState(
        user_id="u_ingest_3",
        phone_number="2348000000003",
        channel="whatsapp",
        last_message_text="Abort",
        last_message_id="msg-3",
        last_callback=None,
    )

    updates = await ingest_message(state)

    assert "last_message_text" not in updates
    assert "last_message_id" not in updates
