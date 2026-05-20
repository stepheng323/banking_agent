from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import ContextFrameReplayModifier, PlannerOutput
from shared.types.quoted_replay import QuotedReplayInterpretation


class _QuotedPlannerStub:
    def __init__(
        self,
        interpretation: QuotedReplayInterpretation,
        replay_modifier: ContextFrameReplayModifier | None = None,
    ) -> None:
        self.interpretation = interpretation
        self.replay_modifier = replay_modifier
        self.quoted_called = False
        self.plan_called = False
        self.last_quoted_context: str | None = None
        self.last_replay_modifier_context: str | None = None

    async def interpret_quoted_replay(self, phone_number: str, text: str, context: str = "None") -> Any:
        del phone_number, text
        self.quoted_called = True
        self.last_quoted_context = context
        return self.interpretation

    async def extract_context_frame_replay_modifiers(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameReplayModifier | None:
        del phone_number, text, path_label
        self.last_replay_modifier_context = context
        return self.replay_modifier

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
                            "recipient_bank_name": "Access Bank",
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
                    "recipient_bank_name": "Access Bank",
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
                            "recipient_bank_name": "Access Bank",
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
                    "recipient_bank_name": "Access Bank",
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
    assert task.payload["source_affinity_mode"] == "auto"


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
                            "source_account_number": "6000000001",
                            "source_affinity_mode": "explicit",
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
                            "source_account_number": "6000000001",
                            "source_affinity_mode": "explicit",
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
                            "source_account_number": "6000000001",
                            "source_affinity_mode": "explicit",
                        },
                        {
                            "task_id": "t_airtime",
                            "task_type": "airtime",
                            "action": "buy_airtime",
                            "amount": 500,
                            "recipient_phone": "08162511023",
                            "network": "MTN",
                            "source_account_id": "acct-1",
                            "source_account_number": "6000000001",
                            "source_affinity_mode": "explicit",
                        },
                    ],
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    assert planner.last_quoted_context is not None
    assert "recipient_phone" in planner.last_quoted_context
    assert "08162511023" in planner.last_quoted_context
    assert "buy_airtime" in planner.last_quoted_context
    assert "source_affinity_mode" in planner.last_quoted_context
    assert {task.type for task in updates["tasks"].values()} == {"transfer", "airtime"}
    assert updates["waves"] == [list(updates["tasks"].keys())]
    assert {task.payload["source_account_number"] for task in updates["tasks"].values()} == {"6000000001"}
    assert {task.payload["source_affinity_mode"] for task in updates["tasks"].values()} == {"explicit"}
    airtime_task = next(task for task in updates["tasks"].values() if task.type == "airtime")
    assert airtime_task.payload["recipient_phone"] == "08162511023"
    assert airtime_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_quoted_replay_normalizes_transfer_account_number_and_source_affinity() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 10000,
                            "recipient_name": "Tolu Adebayo",
                            "recipient_account_number": "2010000001",
                            "recipient_bank_code": "044",
                            "recipient_bank_name": "Access Bank",
                            "source_bank_name": "Access Bank",
                            "source_account_id": "acct-access",
                            "source_account_number": "0000000003",
                        },
                    }
                ],
            }
        )
    )
    state = OrchestratorState(
        user_id="u3accountnumber",
        phone_number="2348000010014",
        channel="telegram",
        has_quote=True,
        quoted_message_id="2955",
        last_message_text="Send this again",
        loaded_context={"language": "en", "user_id": "user-13"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account_number": "2010000001",
                    "recipient_bank_code": "044",
                    "recipient_bank_name": "Access Bank",
                    "source_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_account_number": "0000000003",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    task = next(iter(updates["tasks"].values()))
    assert task.type == "transfer"
    assert task.payload["recipient_account"] == "2010000001"
    assert task.payload["recipient_account_number"] == "2010000001"
    assert task.payload["recipient_bank_code"] == "044"
    assert task.payload["source_affinity_mode"] == "explicit"
    assert task.payload["source_account_number"] == "0000000003"


@pytest.mark.asyncio
async def test_quoted_replay_modifier_applies_source_account_override() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [],
            }
        ),
        replay_modifier=ContextFrameReplayModifier(
            confidence=0.95,
            source_account_reference="gtb",
            source_account_evidence="gtb",
            reason="explicit source override",
        ),
    )
    state = OrchestratorState(
        user_id="u3sourceoverride",
        phone_number="2348000010016",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.16",
        last_message_text="Again, but from gtb",
        loaded_context={
            "language": "en",
            "user_id": "user-16",
            "transaction_accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_name": "Access Main",
                    "account_number": "6000000003",
                },
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_name": "GT Main",
                    "account_number": "6000000002",
                },
            ],
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
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_code": "044",
                    "recipient_bank_name": "Access Bank",
                    "source_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_account_number": "6000000003",
                    "source_affinity_mode": "explicit",
                    "narration": "for rent",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    assert planner.last_replay_modifier_context is not None
    task = next(iter(updates["tasks"].values()))
    assert task.type == "transfer"
    assert task.payload["amount"] == 5000
    assert task.payload["source_account_id"] == "acct-gtb"
    assert task.payload["source_bank_name"] == "GTBank"
    assert task.payload["source_account_name"] == "GT Main"
    assert task.payload["source_account_number"] == "6000000002"
    assert task.payload["source_affinity_mode"] == "explicit"
    assert task.payload["narration"] == "for rent"
    assert task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_quoted_replay_modifier_applies_amount_and_narration_overrides() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [],
            }
        ),
        replay_modifier=ContextFrameReplayModifier(
            confidence=0.95,
            amount=10000,
            amount_evidence="10k",
            narration="school fees",
            narration_evidence="for school fees",
            reason="explicit amount and narration override",
        ),
    )
    state = OrchestratorState(
        user_id="u3amountnarration",
        phone_number="2348000010017",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.17",
        last_message_text="Again, but with 10k for school fees",
        loaded_context={"language": "en", "user_id": "user-17"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 5000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_code": "044",
                    "recipient_bank_name": "Access Bank",
                    "narration": "for rent",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    task = next(iter(updates["tasks"].values()))
    assert task.payload["amount"] == 10000
    assert task.payload["narration"] == "school fees"


@pytest.mark.asyncio
async def test_quoted_replay_modifier_unmatched_source_account_clarifies() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [],
            }
        ),
        replay_modifier=ContextFrameReplayModifier(
            confidence=0.95,
            source_account_reference="uba",
            source_account_evidence="uba",
            reason="explicit source override",
        ),
    )
    state = OrchestratorState(
        user_id="u3sourceunmatched",
        phone_number="2348000010018",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id="wamid.receipt.18",
        last_message_text="Again, but from uba",
        loaded_context={
            "language": "en",
            "user_id": "user-18",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "6000000003",
                }
            ],
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
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_code": "044",
                    "recipient_bank_name": "Access Bank",
                    "source_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_account_number": "6000000003",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert "tasks" not in updates
    assert "could not find 'uba'" in updates["final_response"]


@pytest.mark.asyncio
async def test_quoted_replay_transfer_missing_bank_asks_for_recipient_bank() -> None:
    planner = _QuotedPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 10000,
                            "recipient_name": "Tolu Adebayo",
                            "recipient_account_number": "2010000001",
                        },
                    }
                ],
            }
        )
    )
    state = OrchestratorState(
        user_id="u3missingbank",
        phone_number="2348000010015",
        channel="telegram",
        has_quote=True,
        quoted_message_id="2956",
        last_message_text="Send this again",
        loaded_context={"language": "en", "user_id": "user-13"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(
                {
                    "task_type": "transfer",
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account_number": "2010000001",
                }
            ),
        }
    }

    updates = await plan_tasks(state, config)

    assert planner.quoted_called is True
    assert planner.plan_called is False
    assert "tasks" not in updates
    assert updates["final_response"] == "I can resend that, but I need the missing recipient bank first."


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
                            "recipient_bank_name": "Access Bank",
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
                    "recipient_bank_name": "Access Bank",
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


def _mixed_quoted_payload(*, airtime_status: str = "failed", transfer_status: str = "success") -> dict[str, Any]:
    return {
        "task_type": "batch",
        "task_ids": ["t_transfer", "t_airtime"],
        "task_types": ["transfer", "airtime"],
        "tasks": [
            {
                "task_id": "t_transfer",
                "task_type": "transfer",
                "action": "send_money",
                "amount": 10000,
                "recipient_name": "Tolu Adebayo",
                "recipient_account": "2010000001",
                "recipient_bank_code": "044",
                "recipient_bank_name": "Access Bank",
                "source_account_id": "acct-access",
                "source_account_number": "1234500003",
                "source_bank_name": "Access Bank",
                "source_affinity_mode": "explicit",
                "final_status": transfer_status,
            },
            {
                "task_id": "t_airtime",
                "task_type": "airtime",
                "action": "buy_airtime",
                "amount": 1000,
                "recipient_phone": "08162511023",
                "network": "MTN",
                "source_account_id": "acct-access",
                "source_account_number": "1234500003",
                "source_bank_name": "Access Bank",
                "source_affinity_mode": "explicit",
                "final_status": airtime_status,
                "error_message": "Provider down",
                "failure_category": "provider_unavailable",
            },
        ],
    }


async def _run_seed_replay(
    interpretation_payload: dict[str, Any],
    quoted_payload: dict[str, Any],
) -> dict[str, Any]:
    planner = _QuotedPlannerStub(QuotedReplayInterpretation.model_validate(interpretation_payload))
    state = OrchestratorState(
        user_id="u-seed",
        phone_number="2348000002000",
        channel="telegram",
        has_quote=True,
        quoted_message_id="quoted-seed",
        last_message_text="send again",
        loaded_context={"language": "en", "user_id": "user-seed"},
    )
    config = {
        "configurable": {
            "task_planner": planner,
            "redis_client": None,
            "actionable_message_repo": _ActionableRepoStub(quoted_payload),
        }
    }
    updates = await plan_tasks(state, config)
    assert planner.quoted_called is True
    assert planner.plan_called is False
    return updates


@pytest.mark.asyncio
async def test_quoted_replay_completed_mixed_summary_replays_all_seed_tasks_by_default() -> None:
    updates = await _run_seed_replay(
        {
            "decision": "execute",
            "confidence": 0.95,
            "tasks": [],
        },
        _mixed_quoted_payload(airtime_status="success", transfer_status="success"),
    )

    assert {task.type for task in updates["tasks"].values()} == {"transfer", "airtime"}
    assert {task.payload["source_account_number"] for task in updates["tasks"].values()} == {"1234500003"}
    assert all("final_status" not in task.payload for task in updates["tasks"].values())
    assert all("failure_category" not in task.payload for task in updates["tasks"].values())


@pytest.mark.asyncio
async def test_quoted_replay_failed_scope_replays_only_failed_leg() -> None:
    updates = await _run_seed_replay(
        {
            "decision": "execute",
            "confidence": 0.95,
            "tasks": [],
            "target_statuses": ["failed"],
        },
        _mixed_quoted_payload(),
    )

    assert len(updates["tasks"]) == 1
    task = next(iter(updates["tasks"].values()))
    assert task.type == "airtime"
    assert task.payload["recipient_phone"] == "08162511023"
    assert task.payload["source_account_number"] == "1234500003"


@pytest.mark.asyncio
async def test_quoted_replay_type_scope_replays_only_airtime() -> None:
    updates = await _run_seed_replay(
        {
            "decision": "execute",
            "confidence": 0.95,
            "tasks": [],
            "target_types": ["airtime"],
        },
        _mixed_quoted_payload(airtime_status="success", transfer_status="success"),
    )

    assert len(updates["tasks"]) == 1
    assert next(iter(updates["tasks"].values())).type == "airtime"


@pytest.mark.asyncio
async def test_quoted_replay_status_and_type_scope_replays_successful_transfer() -> None:
    updates = await _run_seed_replay(
        {
            "decision": "execute",
            "confidence": 0.95,
            "tasks": [],
            "target_statuses": ["success"],
            "target_types": ["transfer"],
        },
        _mixed_quoted_payload(),
    )

    assert len(updates["tasks"]) == 1
    task = next(iter(updates["tasks"].values()))
    assert task.type == "transfer"
    assert task.payload["recipient_bank_code"] == "044"


@pytest.mark.asyncio
async def test_quoted_replay_scoped_no_match_asks_specific_clarification() -> None:
    updates = await _run_seed_replay(
        {
            "decision": "execute",
            "confidence": 0.95,
            "tasks": [],
            "target_statuses": ["failed"],
            "target_types": ["transfer"],
        },
        _mixed_quoted_payload(airtime_status="success", transfer_status="success"),
    )

    assert "tasks" not in updates
    assert updates["final_response"] == "I couldn't find a quoted transaction matching that replay request."
