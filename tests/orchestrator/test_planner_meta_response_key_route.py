"""Tests for planner response_key deterministic routing."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

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
    config: RunnableConfig = {"configurable": {"task_planner": planner, "services": {}, "redis_client": None}}

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == render_message("conversational.identity", "en")


@pytest.mark.asyncio
async def test_planner_non_meta_response_key_uses_deterministic_message() -> None:
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
    config: RunnableConfig = {"configurable": {"task_planner": planner, "services": {}, "redis_client": None}}

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == render_message("conversational.checkin", "en")
