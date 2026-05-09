from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannerOutput
from shared.types.quoted_replay import QuotedReplayInterpretation


class _QuotedPlannerStub:
    def __init__(self, interpretation: QuotedReplayInterpretation) -> None:
        self.interpretation = interpretation
        self.quoted_called = False
        self.plan_called = False
        self.last_quoted_context: str | None = None

    async def interpret_quoted_replay(self, phone_number: str, text: str, context: str = "None") -> Any:
        del phone_number, text
        self.quoted_called = True
        self.last_quoted_context = context
        return self.interpretation

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        self.plan_called = True
        return PlannerOutput(
            primary_intent="conversational",
            response="hello",
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


@pytest.mark.asyncio
async def test_quoted_replay_hit_skips_main_planner_and_creates_transfer_task() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.9,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 50000,
                            "recipient_name": "Tolu",
                            "recipient_account": "0123456789",
                        },
                    }
                ],
            }
        )
    )
    state = OrchestratorState(
        user_id="u1",
        phone_number="2348000000001",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.1",
        last_message_text="again but 50k",
        loaded_context={"language": "en", "user_id": "user-1"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 5000,
                    "recipient_name": "Ada",
                    "recipient_account": "0123456789",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    task = updates["tasks"][next(iter(updates["tasks"]))]
    assert task.type == "transfer"
    assert task.payload["amount"] == 50000
    assert task.payload["skip_extraction"] is True
    assert task.payload["confirmation"]["confirmed"] is False
    assert task.payload["idempotency_key"] is None
    assert task.payload["transaction_id"] is None


@pytest.mark.asyncio
async def test_quoted_replay_context_uses_shared_compact_summary() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "not_replay",
                "confidence": 0.85,
                "tasks": [],
            }
        )
    )
    state = OrchestratorState(
        user_id="u1b",
        phone_number="2348000000009",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.9",
        last_message_text="run this one again",
        loaded_context={
            "language": "en",
            "user_id": "user-9",
            "accounts": [{"bank_name": "First Bank", "account_number": "0334557890", "mandate_status": "pending"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "8162511023"}],
        },
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 5000,
                    "recipient_name": "Ada",
                    "recipient_account": "0123456789",
                }
            ),
        }
    }

    await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.last_quoted_context is not None
    assert "QUOTED_MESSAGE_ID=wamid.receipt.9" in planner.last_quoted_context
    assert "QUOTED_ACTIONABLE_PAYLOAD=" in planner.last_quoted_context
    assert "ACCOUNTS:" in planner.last_quoted_context
    assert "BENEFICIARIES:" in planner.last_quoted_context


@pytest.mark.asyncio
async def test_quoted_not_replay_falls_back_to_main_planner() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "not_replay",
                "confidence": 0.8,
                "tasks": [],
            }
        )
    )
    state = OrchestratorState(
        user_id="u2",
        phone_number="2348000000002",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.2",
        last_message_text="what is my balance",
        loaded_context={"language": "en", "user_id": "user-2"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub({"task_type": "transfer", "amount": 5000}),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is True
    assert updates["final_response"] == "hello"


@pytest.mark.asyncio
async def test_quoted_replay_clarify_returns_final_response_without_support_task() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "clarify",
                "confidence": 0.8,
                "tasks": [],
                "clarify_message": "Do you want me to run it again?",
                "reason": "ambiguous replay command",
            }
        )
    )
    state = OrchestratorState(
        user_id="u3",
        phone_number="2348000000003",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.3",
        last_message_text="run it once more maybe",
        loaded_context={"language": "en", "user_id": "user-3"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub({"task_type": "transfer", "amount": 5000}),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    assert updates["final_response"] == "Do you want me to run it again?"
    assert "tasks" not in updates
    assert "pending_interrupt" not in updates


@pytest.mark.asyncio
async def test_simple_quoted_replay_again_uses_replay_interpreter_and_skips_main_planner() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 5000,
                            "recipient_name": "Ada",
                            "recipient_account": "0123456789",
                        },
                    }
                ],
            }
        )
    )
    state = OrchestratorState(
        user_id="u3b",
        phone_number="2348000000013",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.13",
        last_message_text="again",
        loaded_context={"language": "en", "user_id": "user-13"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 5000,
                    "recipient_name": "Ada",
                    "recipient_account": "0123456789",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    task = updates["tasks"][next(iter(updates["tasks"]))]
    assert task.type == "transfer"
    assert task.payload["amount"] == 5000
    assert task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_simple_quoted_replay_batch_payload_replays_all_tasks_from_interpreter() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "action": "send_money",
                            "amount": 5000,
                            "recipient_name": "Tolu",
                            "recipient_account": "2010000003",
                            "recipient_bank_name": "First Bank",
                            "source_account_id": "acct-1",
                        },
                    },
                    {
                        "task_type": "airtime",
                        "payload": {
                            "action": "buy_airtime",
                            "amount": 500,
                            "recipient_phone": "08162511023",
                            "network": "MTN",
                            "source_account_id": "acct-1",
                        },
                    },
                ],
            }
        )
    )
    state = OrchestratorState(
        user_id="u3batch",
        phone_number="2348000010013",
        channel="telegram",
        has_quote=True,
        quoted_message_id="2954",
        last_message_text="Resend this",
        loaded_context={"language": "en", "user_id": "user-13"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "batch",
                    "task_ids": ["t_transfer", "t_airtime"],
                    "task_types": ["transfer", "airtime"],
                    "tasks": [
                        {
                            "task_id": "t_transfer",
                            "task_type": "transfer",
                            "action": "send_money",
                            "amount": 5000,
                            "recipient_name": "Tolu",
                            "recipient_account": "2010000003",
                            "recipient_bank_name": "First Bank",
                            "source_account_id": "acct-1",
                        },
                        {
                            "task_id": "t_airtime",
                            "task_type": "airtime",
                            "action": "buy_airtime",
                            "amount": 500,
                            "recipient_phone": "08162511023",
                            "network": "MTN",
                            "source_account_id": "acct-1",
                        },
                    ],
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    assert {task.type for task in updates["tasks"].values()} == {"transfer", "airtime"}
    assert updates["waves"] == [list(updates["tasks"].keys())]
    airtime_task = next(task for task in updates["tasks"].values() if task.type == "airtime")
    assert airtime_task.payload["recipient_phone"] == "08162511023"
    assert airtime_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_non_trivial_quoted_replay_modification_still_uses_replay_llm() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.9,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 5000,
                            "recipient_name": "Tolu",
                            "recipient_account": "0123456789",
                        },
                    }
                ],
            }
        )
    )
    state = OrchestratorState(
        user_id="u3c",
        phone_number="2348000000014",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.14",
        last_message_text="again and send to tolu",
        loaded_context={"language": "en", "user_id": "user-14"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 5000,
                    "recipient_name": "Ada",
                    "recipient_account": "0123456789",
                }
            ),
        }
    }

    await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False


@pytest.mark.asyncio
async def test_quoted_replay_low_confidence_returns_clarify_response() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.5,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 1000,
                            "recipient_name": "Ada",
                            "recipient_account": "0123456789",
                        },
                    }
                ],
                "reason": "possible replay but weak signal",
            }
        )
    )
    state = OrchestratorState(
        user_id="u4",
        phone_number="2348000000004",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.4",
        last_message_text="do that one",
        loaded_context={"language": "en", "user_id": "user-4"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub({"task_type": "transfer", "amount": 5000}),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    assert "final_response" in updates
    assert "tasks" not in updates
