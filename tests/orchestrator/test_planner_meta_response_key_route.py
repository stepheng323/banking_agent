"""Tests for planner response_key deterministic routing."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.conversation.conversation_responder_intents import (
    SOCIAL_META_INTENT,
    SOCIAL_META_RESPONSE_KEY_CTX,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest import ingest_message
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from banking.presentation.i18n.renderer import render_message
from shared.config.settings import settings
from shared.types.planner import PlannerOutput


class _FakeMetaLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def with_structured_output(self, _schema: Any) -> _FakeMetaLLM:
        return self

    def with_config(self, _config: dict[str, Any]) -> _FakeMetaLLM:
        return self

    async def ainvoke(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._response


class _MockPlanner:
    planner_llm: Any | None

    def __init__(self, output: PlannerOutput, *, planner_llm: Any | None = None) -> None:
        self._output = output
        self.planner_llm = planner_llm

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _FakeConversationResponder:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def generate_reply(
        self,
        text: str,
        user_ctx: dict[str, Any],
        intent: str | None = None,
    ) -> str:
        self.calls.append({"text": text, "user_ctx": dict(user_ctx), "intent": intent})
        return self.reply


def _apply(state: OrchestratorState, updates: dict[str, Any]) -> OrchestratorState:
    return state.model_copy(update=updates)


@pytest.mark.asyncio
async def test_planner_identity_response_key_renders_deterministically() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.identity",
        confidence=0.9,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlanner(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": f"I am {settings.app_name}."}),
    )
    state = OrchestratorState(
        user_id="u_meta_r_1",
        phone_number="2348000001001",
        channel="whatsapp",
        last_message_text="who are you",
    )
    responder = _FakeConversationResponder("This should not be used.")
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "semantic_router_llm": planner,
            "capability_classifier_llm": planner,
            "services": {},
            "redis_client": None,
            "conversation_responder": responder,
        }
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == render_message("conversational.identity", "en")
    assert responder.calls == []


@pytest.mark.asyncio
async def test_planner_social_meta_response_key_uses_conversation_responder() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.checkin",
        confidence=0.9,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlanner(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": "unused"}),
    )
    state = OrchestratorState(
        user_id="u_meta_r_2",
        phone_number="2348000001002",
        channel="whatsapp",
        last_message_text="are you there",
    )
    responder = _FakeConversationResponder("I'm here, ready for transfers or balance checks.")
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "semantic_router_llm": planner,
            "capability_classifier_llm": planner,
            "services": {},
            "redis_client": None,
            "conversation_responder": responder,
        }
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == responder.reply
    assert responder.calls
    assert responder.calls[0]["intent"] == SOCIAL_META_INTENT
    assert responder.calls[0]["user_ctx"][SOCIAL_META_RESPONSE_KEY_CTX] == "conversational.checkin"


@pytest.mark.asyncio
async def test_planner_social_meta_response_key_falls_back_without_responder() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.checkin",
        confidence=0.9,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlanner(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": "unused"}),
    )
    state = OrchestratorState(
        user_id="u_meta_r_2b",
        phone_number="2348000001003",
        channel="whatsapp",
        last_message_text="are you there",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "semantic_router_llm": planner,
            "capability_classifier_llm": planner,
            "services": {},
            "redis_client": None,
        }
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == render_message("conversational.checkin", "en")
