"""Finalize node behavior for capability-blocked failures."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize import finalize


@pytest.mark.asyncio
async def test_finalize_outputs_clean_message_for_capability_blocked_failure():
    """Capability-blocked failures should not be prefixed with 'Failed:'."""
    state = OrchestratorState(
        user_id="u_1",
        phone_number="2348000000000",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.FAILED,
                payload={
                    "error": "Scheduled transfers aren't available yet. I can help you make a one-time transfer now.",
                    "capability_blocked": True,
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.FAILED,
                payload={"error": "Bank provider timeout"},
            ),
            "t3": TaskSpec(
                id="t3",
                type="transfer",
                stage=TaskStage.FAILED,
                payload={
                    "error": (
                        "Execution failed: This Session's transaction has been rolled back. "
                        "[SQL: INSERT INTO transactions ...]"
                    )
                },
            ),
        },
    )

    config: RunnableConfig = {
        "configurable": {
            "beneficiary_suggestion_service": None,
            "redis_client": None,
            "queue": None,
        }
    }

    updates = await finalize(state, config)
    outbox = updates["outbox"]

    assert outbox[0]["text"].startswith("Scheduled transfers aren't available yet.")
    assert not outbox[0]["text"].startswith("Failed:")
    assert outbox[1]["text"] == "Failed: Bank provider timeout"
    assert outbox[2]["text"] == "Failed: Transfer could not be completed. Please try again."
    assert "[SQL:" not in outbox[2]["text"]
    assert "Session's transaction" not in outbox[2]["text"]
