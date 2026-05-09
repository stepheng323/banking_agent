"""Finalize node tests for stashed session resume prompts."""

import time
import uuid

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.finalize import finalize


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "beneficiary_suggestion_service": None,
            "redis_client": None,
            "queue": None,
        }
    }


def _stashed(intent: str = "transfer") -> list[dict]:
    now_ts = int(time.time())
    return [
        {
            "tasks": {"t_stashed": TaskSpec(id="t_stashed", type="transfer", stage=TaskStage.EXTRACTED, payload={})},
            "waves": [["t_stashed"]],
            "current_wave_index": 0,
            "pending_interrupt": {
                "kind": "input",
                "task_ids": ["t_stashed"],
                "fields_by_task": {"t_stashed": ["amount"]},
                "prompt": "Enter amount",
            },
            "intent": intent,
            "stashed_at_ts": now_ts,
        }
    ]


@pytest.mark.asyncio
async def test_finalize_stashed_and_completed_appends_resume_prompt() -> None:
    state = OrchestratorState(
        user_id="u_resume_1",
        phone_number="2348000000011",
        channel="whatsapp",
        tasks={"t1": TaskSpec(id="t1", type="account", stage=TaskStage.COMPLETED, payload={})},
        stashed_sessions=_stashed(intent="transfer"),
    )

    updates = await finalize(state, _config())

    assert updates["outbox"][-1]["text"] == "Would you like to resume your transfer?"
    assert "context_frames" in updates
    assert updates["context_frames"][-1].items[0].data["resume_prompt"] is True


@pytest.mark.asyncio
async def test_finalize_stashed_and_failed_only_does_not_prompt_resume() -> None:
    state = OrchestratorState(
        user_id="u_resume_2",
        phone_number="2348000000012",
        channel="whatsapp",
        tasks={"t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.FAILED, payload={"error": "Provider timeout"})},
        stashed_sessions=_stashed(intent="transfer"),
    )

    updates = await finalize(state, _config())

    assert "Would you like to resume your" not in updates["outbox"][-1]["text"]
    assert "context_frames" not in updates


@pytest.mark.asyncio
async def test_finalize_stashed_and_cancelled_only_does_not_prompt_resume() -> None:
    state = OrchestratorState(
        user_id="u_resume_3",
        phone_number="2348000000013",
        channel="whatsapp",
        tasks={"t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.CANCELLED, payload={})},
        stashed_sessions=_stashed(intent="transfer"),
    )

    updates = await finalize(state, _config())

    assert updates["outbox"], "Cancelled flow should still emit a cancellation reply."
    assert "Would you like to resume your" not in updates["outbox"][-1]["text"]
    assert "context_frames" not in updates


@pytest.mark.asyncio
async def test_finalize_multiple_cancelled_tasks_emits_single_cancel_message() -> None:
    state = OrchestratorState(
        user_id="u_resume_6",
        phone_number="2348000000016",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.CANCELLED, payload={}),
            "t2": TaskSpec(id="t2", type="transfer", stage=TaskStage.CANCELLED, payload={}),
        },
    )

    updates = await finalize(state, _config())

    cancelled_lines = [
        entry.get("text")
        for entry in updates["outbox"]
        if entry.get("type") == "say" and "Transaction cancelled" in str(entry.get("text"))
    ]
    assert len(cancelled_lines) == 1


@pytest.mark.asyncio
async def test_finalize_stashed_with_no_terminal_tasks_does_not_prompt_resume() -> None:
    state = OrchestratorState(
        user_id="u_resume_4",
        phone_number="2348000000014",
        channel="whatsapp",
        tasks={"t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.EXTRACTED, payload={})},
        stashed_sessions=_stashed(intent="transfer"),
    )

    updates = await finalize(state, _config())

    assert updates["outbox"] == []
    assert "context_frames" not in updates


@pytest.mark.asyncio
async def test_finalize_stashed_and_completed_transfer_does_not_prompt_resume() -> None:
    state = OrchestratorState(
        user_id="u_resume_5",
        phone_number="2348000000015",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={"amount": 5000, "recipient_name": "Fatima"},
            )
        },
        stashed_sessions=_stashed(intent="transfer"),
    )

    updates = await finalize(state, _config())

    assert updates["outbox"], "Completed transfer should emit processing text."
    assert "Would you like to resume your" not in updates["outbox"][-1]["text"]
    assert updates["context_frames"][-1].frame_type == ContextFrameType.TRANSACTION_DETAIL
    assert updates["context_frames"][-1].items[0].data["amount"] == 5000
    assert updates["context_frames"][-1].items[0].data["recipient_name"] == "Fatima"


@pytest.mark.asyncio
async def test_finalize_stale_stash_does_not_prompt_resume_and_cleans_stash() -> None:
    stale_stash = [
        {
            "tasks": {"t_stashed": TaskSpec(id="t_stashed", type="transfer", stage=TaskStage.EXTRACTED, payload={})},
            "waves": [["t_stashed"]],
            "current_wave_index": 0,
            "pending_interrupt": {
                "kind": "input",
                "task_ids": ["t_stashed"],
                "fields_by_task": {"t_stashed": ["amount"]},
                "prompt": "Enter amount",
            },
            "intent": "transfer",
        }
    ]
    state = OrchestratorState(
        user_id="u_resume_7",
        phone_number="2348000000017",
        channel="whatsapp",
        tasks={"t1": TaskSpec(id="t1", type="account", stage=TaskStage.COMPLETED, payload={})},
        stashed_sessions=stale_stash,
    )

    updates = await finalize(state, _config())

    assert updates["outbox"] == []
    assert "context_frames" not in updates
    assert updates["stashed_sessions"] == []


@pytest.mark.asyncio
async def test_finalize_does_not_repeat_resume_prompt_with_live_resume_frame() -> None:
    now_ts = int(time.time())
    existing_frame = ContextFrame(
        frame_id=str(uuid.uuid4()),
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_type=EntityType.GENERIC,
                entity_id="resumption_prompt",
                label="Resume transfer",
                data={"intent": "transfer", "resume_prompt": True},
            )
        ],
        created_at_ts=now_ts,
        ttl_seconds=300,
    )
    state = OrchestratorState(
        user_id="u_resume_8",
        phone_number="2348000000018",
        channel="whatsapp",
        tasks={"t1": TaskSpec(id="t1", type="account", stage=TaskStage.COMPLETED, payload={})},
        stashed_sessions=_stashed(intent="transfer"),
        context_frames=[existing_frame],
    )

    updates = await finalize(state, _config())

    assert updates["outbox"] == []
    assert "context_frames" not in updates


@pytest.mark.asyncio
async def test_finalize_mixed_transaction_batch_emits_processing_only() -> None:
    state = OrchestratorState(
        user_id="u_resume_9",
        phone_number="2348000000019",
        channel="telegram",
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 20000,
                    "recipient_name": "Mum",
                    "recipient_resolved_name": "Mercy Johnson",
                    "recipient_bank_name": "Opay",
                    "recipient_account": "8162511023",
                    "receipt": {"status": "processing"},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                    "receipt": {
                        "status": "queued",
                        "message": "Your airtime purchase of ₦1,000.00 for 08162511023 (MTN) is being processed.",
                    },
                },
            ),
        },
    )

    updates = await finalize(state, _config())

    say_entries = [entry for entry in updates["outbox"] if entry.get("type") == "say"]
    assert len(say_entries) == 1
    assert say_entries[0]["text"] == "Your transactions are being processed."
    assert updates["context_frames"][-1].frame_type == ContextFrameType.TRANSACTION_LIST
    assert [item.data["transaction_type"] for item in updates["context_frames"][-1].items] == ["transfer", "airtime"]
    assert updates["context_frames"][-1].items[1].data["phone"] == "08162511023"


@pytest.mark.asyncio
async def test_finalize_queued_transfer_batch_does_not_emit_completed_summary() -> None:
    state = OrchestratorState(
        user_id="u_resume_11",
        phone_number="2348000000021",
        channel="telegram",
        loaded_context={"language": "en"},
        tasks={
            "t_transfer_1": TaskSpec(
                id="t_transfer_1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 10000,
                    "recipient_name": "Mum",
                    "recipient_resolved_name": "Mercy Johnson",
                    "receipt": {"status": "queued"},
                },
            ),
            "t_transfer_2": TaskSpec(
                id="t_transfer_2",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adedayo",
                    "receipt": {"status": "queued"},
                },
            ),
        },
    )

    updates = await finalize(state, _config())

    say_entries = [entry for entry in updates["outbox"] if entry.get("type") == "say"]
    assert len(say_entries) == 1
    assert say_entries[0]["text"] == "Your transactions are being processed."
    assert "Transaction Summary" not in say_entries[0]["text"]
    assert "All transactions completed successfully" not in say_entries[0]["text"]


@pytest.mark.asyncio
async def test_finalize_completed_transfer_clears_interrupt_state() -> None:
    state = OrchestratorState(
        user_id="u_resume_10",
        phone_number="2348000000020",
        channel="telegram",
        loaded_context={"language": "en"},
        pending_interrupt={"kind": "input", "task_ids": ["t1"], "fields_by_task": {"t1": ["amount"]}},
        last_interrupt={"kind": "input", "task_ids": ["t1"], "fields_by_task": {"t1": ["amount"]}},
        session_stack=[],
        active_domain="transfer",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={"amount": 5000, "recipient_name": "Mum"},
            )
        },
    )

    updates = await finalize(state, _config())

    assert updates["pending_interrupt"] is None
    assert updates["last_interrupt"] is None
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
