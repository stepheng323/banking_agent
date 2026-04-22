import json
from typing import Any

from apps.core.src.agent.orchestrator.nodes.gate.runner import (
    _resolve_beneficiary_suggestion_reply,
    session_gate_direct_path,
)
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.types.planner import SemanticRouteDecision


class _SuggestionRedis:
    def __init__(self, suggestion_payload: dict[str, Any]) -> None:
        self._payload = suggestion_payload
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return json.dumps(self._payload)
        return None

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1


class _RouteTurnPlanner:
    def __init__(self, decision: SemanticRouteDecision) -> None:
        self._decision = decision
        self.route_calls = 0

    async def route_semantic_turn(self, phone_number: str, text: str, context: str = "None") -> SemanticRouteDecision:
        del phone_number, text, context
        self.route_calls += 1
        return self._decision


def test_beneficiary_suggestion_resolver_extracts_noisy_alias() -> None:
    decision = _resolve_beneficiary_suggestion_reply(
        "abeg yes save am as Mum please",
        locale="pcm",
        suggestion_payload={"recipient_name": "Tolu"},
    )
    assert decision.action == "save_alias"
    assert decision.alias == "Mum"


def test_beneficiary_suggestion_resolver_dismisses_transaction_message() -> None:
    decision = _resolve_beneficiary_suggestion_reply(
        "Send 10k to mum",
        locale="en",
        suggestion_payload={"recipient_name": "Tolu"},
    )
    assert decision.action == "dismiss"
    assert decision.reason == "transaction_guard"


def test_beneficiary_suggestion_resolver_treats_bare_alias_as_save_alias() -> None:
    decision = _resolve_beneficiary_suggestion_reply(
        "Tols",
        locale="en",
        suggestion_payload={"recipient_name": "Mercy Johnson"},
    )
    assert decision.action == "save_alias"
    assert decision.alias == "Tols"
    assert decision.reason == "bare_alias_reply"


def test_beneficiary_suggestion_resolver_cleans_bare_alias_trailing_noise() -> None:
    decision = _resolve_beneficiary_suggestion_reply(
        "Tols please",
        locale="en",
        suggestion_payload={"recipient_name": "Mercy Johnson"},
    )
    assert decision.action == "save_alias"
    assert decision.alias == "Tols"


def test_beneficiary_suggestion_resolver_allows_two_word_bare_alias() -> None:
    decision = _resolve_beneficiary_suggestion_reply(
        "Big Tols",
        locale="en",
        suggestion_payload={"recipient_name": "Mercy Johnson"},
    )
    assert decision.action == "save_alias"
    assert decision.alias == "Big Tols"


async def test_gate_suggestion_save_alias_creates_beneficiary_task_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.95,
            detected_language="English",
            response_key="conversational.checkin",
            response=None,
            expected_transaction_executors=[],
            reason="not-used",
        )
    )
    redis_client = _SuggestionRedis(
        {
            "recipient_name": "Tolu Adedayo",
            "recipient_account": "0760505261",
            "bank_name": "First Bank",
        }
    )
    state = OrchestratorState(
        user_id="u_gate_benef_1",
        phone_number="2348011112201",
        channel="whatsapp",
        last_message_text="save as Mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"redis_client": redis_client, "task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["waves"] == [["direct_beneficiary_save"]]
    task = updates["tasks"]["direct_beneficiary_save"]
    assert task.type == "beneficiary"
    assert task.payload["action"] == "save_beneficiary"
    assert task.payload["alias"] == "Mum"
    assert redis_client.deleted_keys == []


async def test_gate_suggestion_affirmation_creates_default_save_task() -> None:
    redis_client = _SuggestionRedis(
        {
            "recipient_name": "Tolu Adedayo",
            "recipient_account": "0760505261",
            "bank_name": "First Bank",
        }
    )
    state = OrchestratorState(
        user_id="u_gate_benef_2",
        phone_number="2348011112202",
        channel="whatsapp",
        last_message_text="yes",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["waves"] == [["direct_beneficiary_save"]]
    task = updates["tasks"]["direct_beneficiary_save"]
    assert task.type == "beneficiary"
    assert task.payload["action"] == "save_beneficiary"
    assert "alias" not in task.payload
    assert redis_client.deleted_keys == []


async def test_gate_suggestion_transaction_turn_dismisses_and_falls_through() -> None:
    redis_client = _SuggestionRedis(
        {
            "recipient_name": "Tolu Adedayo",
            "recipient_account": "0760505261",
            "bank_name": "First Bank",
        }
    )
    state = OrchestratorState(
        user_id="u_gate_benef_3",
        phone_number="2348011112203",
        channel="whatsapp",
        last_message_text="Send 10k to mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert "turn_context_summary" in updates
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_transfer_domain"
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "fresh_transfer_command"
    task = updates["tasks"]["direct_transfer"]
    assert task.type == "transfer"
    assert task.payload["message"] == "Send 10k to mum"
    assert redis_client.deleted_keys == ["user:2348011112203:beneficiary_suggestion"]


async def test_gate_suggestion_non_save_reply_dismisses_and_falls_through() -> None:
    redis_client = _SuggestionRedis(
        {
            "recipient_name": "Tolu Adedayo",
            "recipient_account": "0760505261",
            "bank_name": "First Bank",
        }
    )
    state = OrchestratorState(
        user_id="u_gate_benef_4",
        phone_number="2348011112204",
        channel="whatsapp",
        last_message_text="no thanks",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert "turn_context_summary" in updates
    assert updates.get("semantic_path_shape") is None
    assert redis_client.deleted_keys == ["user:2348011112204:beneficiary_suggestion"]


async def test_gate_suggestion_bare_alias_creates_beneficiary_task_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.95,
            detected_language="English",
            response_key="conversational.checkin",
            response=None,
            expected_transaction_executors=[],
            reason="not-used",
        )
    )
    redis_client = _SuggestionRedis(
        {
            "recipient_name": "Mercy Johnson",
            "recipient_account": "0334555167",
            "bank_name": "GTBank",
        }
    )
    state = OrchestratorState(
        user_id="u_gate_benef_5",
        phone_number="2348011112205",
        channel="whatsapp",
        last_message_text="Tols",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"redis_client": redis_client, "task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["waves"] == [["direct_beneficiary_save"]]
    task = updates["tasks"]["direct_beneficiary_save"]
    assert task.type == "beneficiary"
    assert task.payload["action"] == "save_beneficiary"
    assert task.payload["alias"] == "Tols"
    assert redis_client.deleted_keys == []
