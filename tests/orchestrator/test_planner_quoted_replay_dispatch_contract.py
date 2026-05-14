from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannerOutput
from shared.types.quoted_replay import QuotedReplayInterpretation


class _PlannerStub:
    def __init__(self, interpretation: QuotedReplayInterpretation) -> None:
        self.interpretation = interpretation
        self.plan_called = False

    async def interpret_quoted_replay(self, phone_number: str, text: str, context: str = "None") -> Any:
        del phone_number, text, context
        return self.interpretation

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> Any:
        del phone_number, text, context
        self.plan_called = True
        return PlannerOutput(
            primary_intent="conversational",
            response="fallback planner",
            tasks=[],
        )


class _ActionableRepoStub:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload

    async def get_by_channel_message_id_for_user(self, channel_message_id: str, user_id: str) -> Any:
        del channel_message_id, user_id
        if self.payload is None:
            return None
        return type("Actionable", (), {"message_data": self.payload})()


def _state() -> OrchestratorState:
    return OrchestratorState(
        user_id="u-dispatch",
        phone_number="2348000000999",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.dispatch",
        last_message_text="again",
        loaded_context={"language": "en", "user_id": "u-dispatch"},
    )


@pytest.mark.asyncio
async def test_dispatch_enforces_skip_extraction_and_fresh_execution_fields() -> None:
    planner = _PlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.91,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "action": "send_money",
                            "amount": 2500,
                            "recipient_name": "Ada",
                            "recipient_account": "0123456789",
                            "recipient_bank_name": "Access Bank",
                            "confirmation": {"confirmed": True},
                            "idempotency_key": "old-key",
                            "transaction_id": "old-id",
                        },
                    }
                ],
            }
        )
    )

    updates = await plan_tasks(
        _state(),
        {
            "configurable": {
                "task_planner": planner,
                "redis_client": None,
                "actionable_message_repo": _ActionableRepoStub({"transaction_id": "tx-1"}),
            }
        },
    )

    task = updates["tasks"][next(iter(updates["tasks"]))]
    assert task.type == "transfer"
    assert task.payload["skip_extraction"] is True
    assert task.payload["confirmation"]["confirmed"] is False
    assert task.payload["idempotency_key"] is None
    assert task.payload["transaction_id"] is None
    assert planner.plan_called is False


@pytest.mark.asyncio
async def test_dispatch_clarifies_when_replay_payload_is_insufficient() -> None:
    planner = _PlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.93,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 2500,
                        },
                    }
                ],
                "clarify_message": "Please tell me who to send to.",
            }
        )
    )

    updates = await plan_tasks(
        _state(),
        {
            "configurable": {
                "task_planner": planner,
                "redis_client": None,
                "actionable_message_repo": _ActionableRepoStub({"transaction_id": "tx-1"}),
            }
        },
    )

    assert "tasks" not in updates
    assert updates["final_response"] == (
        "I can resend that, but I need the missing recipient account number, and recipient bank first."
    )
    assert planner.plan_called is False


@pytest.mark.asyncio
async def test_dispatch_clarifies_when_actionable_seed_missing() -> None:
    planner = _PlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.9,
                "tasks": [
                    {
                        "task_type": "airtime",
                        "payload": {
                            "amount": 1000,
                            "recipient_phone": "08010000000",
                        },
                    }
                ],
            }
        )
    )

    updates = await plan_tasks(
        _state(),
        {
            "configurable": {
                "task_planner": planner,
                "redis_client": None,
                "actionable_message_repo": _ActionableRepoStub(None),
            }
        },
    )

    assert planner.plan_called is False
    assert updates["final_response"]


@pytest.mark.asyncio
async def test_dispatch_accepts_transfer_payload_seeded_by_beneficiary_id() -> None:
    planner = _PlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.9,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 6000,
                            "beneficiary_id": "bene-1",
                        },
                    }
                ],
            }
        )
    )

    updates = await plan_tasks(
        _state(),
        {
            "configurable": {
                "task_planner": planner,
                "redis_client": None,
                "actionable_message_repo": _ActionableRepoStub({"transaction_id": "tx-1"}),
            }
        },
    )

    task = updates["tasks"][next(iter(updates["tasks"]))]
    assert task.type == "transfer"
    assert task.payload["beneficiary_id"] == "bene-1"
    assert task.payload["skip_extraction"] is True
    assert planner.plan_called is False
