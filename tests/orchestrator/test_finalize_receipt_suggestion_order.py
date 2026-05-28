from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize import finalize


class _SuggestionServiceStub:
    async def check_and_suggest_beneficiary(self, **_: Any) -> str:
        return "Would you like to save Mercy Johnson?"


@pytest.mark.asyncio
async def test_finalize_shows_beneficiary_prompt_after_receipt_choice() -> None:
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
            "publisher": queue,
        }
    }

    updates = await finalize(state, config)

    assert updates["outbox"] and len(updates["outbox"]) == 2
    receipt_choice = updates["outbox"][0]
    assert receipt_choice["type"] == "show_options"
    assert "Would you like to save" not in receipt_choice["title"]
    payload = receipt_choice["actionable_payload"]["receipt_job"]
    assert "beneficiary_suggestion_message" not in payload
    assert updates["outbox"][1] == {"type": "say", "text": "Would you like to save Mercy Johnson?"}
    queue.publish.assert_not_awaited()


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
            "publisher": queue,
        }
    }

    updates = await finalize(state, config)

    queue.publish.assert_not_awaited()
    receipt_choice = updates["outbox"][0]
    payload = receipt_choice["actionable_payload"]["receipt_job"]
    assert "beneficiary_suggestion_message" not in payload
    assert updates["outbox"][1] == {"type": "say", "text": "Would you like to save Mercy Johnson?"}
    assert "Would you like to resume your transfer?" not in receipt_choice["title"]


@pytest.mark.asyncio
async def test_finalize_emits_receipt_choice_with_graph_handler_config() -> None:
    queue = AsyncMock()
    state = OrchestratorState(
        user_id="u_receipt_defer_3",
        phone_number="2348099999997",
        channel="telegram",
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
                    "transaction_id": "TRX-997",
                    "receipt": {"status": "success"},
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "beneficiary_suggestion_service": _SuggestionServiceStub(),
            "redis_client": None,
            "publisher": queue,
        }
    }

    updates = await finalize(state, config)

    queue.publish.assert_not_awaited()
    receipt_choice = updates["outbox"][0]
    assert receipt_choice["type"] == "show_options"
    assert receipt_choice["actionable_payload"]["receipt_job"]["transaction_reference"] == "TRX-997"


@pytest.mark.asyncio
async def test_finalize_defers_airtime_beneficiary_prompt_for_single_async_transaction() -> None:
    state = OrchestratorState(
        user_id="u_airtime_suggestion_1",
        phone_number="2348099999996",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 2000.0,
                    "recipient_phone": "08031234567",
                    "network": "MTN",
                    "transaction_id": "AIR-999",
                    "receipt": {"status": "processing", "message": "Airtime is processing."},
                },
            )
        },
    )
    config: RunnableConfig = {"configurable": {"beneficiary_suggestion_service": _SuggestionServiceStub()}}

    updates = await finalize(state, config)

    assert len(updates["outbox"]) == 1
    text = updates["outbox"][0]["text"]
    assert "Airtime is processing." in text
    assert "Would you like to save Mercy Johnson?" not in text


@pytest.mark.asyncio
async def test_finalize_defers_data_beneficiary_prompt_for_single_async_transaction() -> None:
    state = OrchestratorState(
        user_id="u_data_suggestion_1",
        phone_number="2348099999994",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="data",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 3500.0,
                    "target_phone": "08031234567",
                    "network": "MTN",
                    "plan_name": "MTN 5 GB data bundle",
                    "transaction_id": "DATA-999",
                    "receipt": {"status": "processing", "message": "Data is processing."},
                },
            )
        },
    )
    config: RunnableConfig = {"configurable": {"beneficiary_suggestion_service": _SuggestionServiceStub()}}

    updates = await finalize(state, config)

    assert len(updates["outbox"]) == 1
    text = updates["outbox"][0]["text"]
    assert "Data is processing." in text
    assert "Would you like to save Mercy Johnson?" not in text


@pytest.mark.asyncio
async def test_finalize_does_not_suggest_for_transfer_batch() -> None:
    state = OrchestratorState(
        user_id="u_batch_suggestion_1",
        phone_number="2348099999995",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 5000.0,
                    "is_batch": True,
                    "recipients": [
                        {
                            "amount": 5000.0,
                            "recipient_name": "Mercy Johnson",
                            "recipient_account": "8162511023",
                            "recipient_bank_name": "Opay",
                        }
                    ],
                    "transaction_id": "TRX-BATCH-1",
                    "receipt": {"status": "success"},
                },
            )
        },
    )
    suggestion_service = _SuggestionServiceStub()
    config: RunnableConfig = {"configurable": {"beneficiary_suggestion_service": suggestion_service}}

    updates = await finalize(state, config)

    assert all("Would you like to save" not in item.get("text", "") for item in updates["outbox"])


@pytest.mark.asyncio
async def test_finalize_does_not_suggest_for_scheduled_single_transfer() -> None:
    state = OrchestratorState(
        user_id="u_scheduled_suggestion_1",
        phone_number="2348099999992",
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
                    "transaction_id": "TRX-SCHEDULED-1",
                    "scheduled_meta": {"run_source": "scheduled"},
                    "receipt": {"status": "success"},
                },
            )
        },
    )
    suggestion_service = _SuggestionServiceStub()
    config: RunnableConfig = {"configurable": {"beneficiary_suggestion_service": suggestion_service}}

    updates = await finalize(state, config)

    assert all("Would you like to save" not in item.get("text", "") for item in updates["outbox"])


@pytest.mark.asyncio
async def test_finalize_does_not_suggest_for_grouped_data_transaction() -> None:
    state = OrchestratorState(
        user_id="u_data_batch_suggestion_1",
        phone_number="2348099999993",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="data",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 3500.0,
                    "target_phone": "08031234567",
                    "network": "MTN",
                    "plan_name": "MTN 5 GB data bundle",
                    "transaction_id": "DATA-BATCH-1",
                    "async_group_size": 2,
                    "receipt": {"status": "processing", "message": "Data is processing."},
                },
            )
        },
    )
    suggestion_service = _SuggestionServiceStub()
    config: RunnableConfig = {"configurable": {"beneficiary_suggestion_service": suggestion_service}}

    updates = await finalize(state, config)

    assert all("Would you like to save" not in item.get("text", "") for item in updates["outbox"])
