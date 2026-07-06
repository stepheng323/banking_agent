"""Tests for callback turn handling in ingest node."""

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest import ingest_message


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


@pytest.mark.asyncio
async def test_day_rollover_clears_stale_session_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest._current_session_date", lambda: "2026-03-10"
    )
    state = OrchestratorState(
        user_id="u_ingest_4",
        phone_number="2348000000004",
        channel="whatsapp",
        last_message_text="hi",
        last_activity_date="2026-03-09",
        tasks={"t1": {"id": "t1", "type": "query", "stage": "draft", "payload": {"message": "more"}}},
        waves=[["t1"]],
        current_wave_index=0,
        task_results={"t1": {"status": "done"}},
        pending_interrupt={"kind": "input", "task_ids": ["t1"]},
        pin_verified=True,
        session_stack=[{"domain": "query", "state": "RUNNING", "interrupt_policy": "ALLOW"}],
        active_domain="query",
        stashed_sessions=[{"intent": "transfer", "tasks": {}}],
    )

    updates = await ingest_message(state)

    assert updates["last_activity_date"] == "2026-03-10"
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0
    assert updates["task_results"] == {}
    assert updates["pending_interrupt"] is None
    assert updates["pin_verified"] is False
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert updates["stashed_sessions"] == []


@pytest.mark.asyncio
async def test_same_day_keeps_existing_session_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest._current_session_date", lambda: "2026-03-10"
    )
    state = OrchestratorState(
        user_id="u_ingest_5",
        phone_number="2348000000005",
        channel="whatsapp",
        last_message_text="more",
        last_activity_date="2026-03-10",
        tasks={"t1": {"id": "t1", "type": "query", "stage": "draft", "payload": {"message": "more"}}},
        waves=[["t1"]],
        pending_interrupt={"kind": "input", "task_ids": ["t1"], "fields_by_task": {"t1": ["message"]}},
    )

    updates = await ingest_message(state)

    assert updates["last_activity_date"] == "2026-03-10"
    assert "tasks" not in updates
    assert "waves" not in updates


@pytest.mark.asyncio
async def test_same_day_clears_terminal_only_stale_state() -> None:
    state = OrchestratorState(
        user_id="u_ingest_6",
        phone_number="2348000000006",
        channel="telegram",
        last_message_text="send 10k",
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.COMPLETED,
                payload={"receipt": {"status": "processing"}},
            )
        },
        waves=[["t_airtime"]],
        current_wave_index=0,
        session_stack=[{"domain": "airtime", "state": "WAITING_FOR_AUTH", "interrupt_policy": "CONFIRM"}],
        active_domain="airtime",
        pin_verified=True,
    )

    updates = await ingest_message(state)

    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert updates["pin_verified"] is False


@pytest.mark.asyncio
async def test_same_day_keeps_blocked_non_terminal_state() -> None:
    state = OrchestratorState(
        user_id="u_ingest_7",
        phone_number="2348000000007",
        channel="telegram",
        last_message_text="continue",
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 10000},
            )
        },
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt={
            "kind": "input",
            "task_ids": ["t_transfer"],
            "fields_by_task": {"t_transfer": ["recipient_account"]},
            "prompt": "Please share the account number.",
        },
    )

    updates = await ingest_message(state)

    assert "tasks" not in updates
    assert "waves" not in updates


@pytest.mark.asyncio
async def test_same_day_clears_unblocked_non_terminal_state() -> None:
    state = OrchestratorState(
        user_id="u_ingest_8",
        phone_number="2348000000008",
        channel="telegram",
        last_message_text="How many scheduled transaction is pending",
        tasks={
            "schedule_count": TaskSpec(
                id="schedule_count",
                type="schedule",
                stage=TaskStage.DRAFT,
                payload={"action": "list_scheduled_transactions"},
            )
        },
        waves=[["schedule_count"]],
        current_wave_index=0,
    )

    updates = await ingest_message(state)

    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0
    assert updates["planner_output"] is None
    assert updates["normalized_instruction"] is None
