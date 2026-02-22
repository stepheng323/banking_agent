from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.finalize import finalize


class _SuggestionServiceStub:
    async def check_and_suggest_beneficiary(self, **_: Any) -> str:
        return "Would you like to save Mercy Johnson?"


@pytest.mark.asyncio
async def test_finalize_defers_beneficiary_prompt_to_receipt_job() -> None:
    queue = AsyncMock()
    state = OrchestratorState(
        user_id="u_receipt_defer_1",
        phone_number="2348099999999",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 5000.0,
                    "recipient_name": "Mercy Johnson",
                    "recipient_resolved_name": "MERCY JOHNSON",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                    "recipient_bank_code": "100004",
                    "source_bank_name": "First Bank",
                    "source_account_name": "Gaines",
                    "transaction_id": "TRX-999",
                    "receipt": {"status": "success"},
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "beneficiary_suggestion_service": _SuggestionServiceStub(),
            "redis_client": None,
            "queue": queue,
        }
    }

    updates = await finalize(state, config)

    assert updates["outbox"] and len(updates["outbox"]) == 1
    assert "Would you like to save" not in updates["outbox"][0]["text"]
    queue.enqueue.assert_awaited_once()
    args = cast(tuple[Any, ...], queue.enqueue.await_args.args)
    payload = cast(dict[str, Any], args[1])
    assert payload.get("beneficiary_suggestion_message") == "Would you like to save Mercy Johnson?"


@pytest.mark.asyncio
async def test_finalize_keeps_beneficiary_suggestion_when_stashed_session_exists_without_resume_prompt() -> None:
    queue = AsyncMock()
    state = OrchestratorState(
        user_id="u_receipt_defer_2",
        phone_number="2348099999998",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 5000.0,
                    "recipient_name": "Mercy Johnson",
                    "recipient_resolved_name": "MERCY JOHNSON",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                    "source_bank_name": "First Bank",
                    "source_account_name": "Gaines",
                    "transaction_id": "TRX-998",
                    "receipt": {"status": "success"},
                },
            )
        },
        stashed_sessions=[
            {
                "tasks": {},
                "waves": [],
                "current_wave_index": 0,
                "pending_interrupt": None,
                "intent": "transfer",
            }
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "beneficiary_suggestion_service": _SuggestionServiceStub(),
            "redis_client": None,
            "queue": queue,
        }
    }

    updates = await finalize(state, config)

    queue.enqueue.assert_awaited_once()
    args = cast(tuple[Any, ...], queue.enqueue.await_args.args)
    payload = cast(dict[str, Any], args[1])
    assert payload.get("beneficiary_suggestion_message") == "Would you like to save Mercy Johnson?"
    assert "Would you like to resume your transfer?" not in updates["outbox"][-1]["text"]
