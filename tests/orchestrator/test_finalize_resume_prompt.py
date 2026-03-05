"""Finalize node tests for stashed session resume prompts."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.finalize import finalize


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "beneficiary_suggestion_service": None,
            "redis_client": None,
            "queue": None,
        }
    }


def _stashed(intent: str = "transfer") -> list[dict]:
    return [
        {
            "tasks": {"t_stashed": TaskSpec(id="t_stashed", type="transfer", stage=TaskStage.EXTRACTED, payload={})},
            "waves": [["t_stashed"]],
            "current_wave_index": 0,
            "pending_interrupt": None,
            "intent": intent,
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
    assert "context_frames" not in updates
