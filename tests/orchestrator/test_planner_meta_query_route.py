"""Tests for planner fallback meta-query routing."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.ingest import ingest_message
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.i18n import render_message
from shared.types.planner import PlannerOutput


class _FakeMetaLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def with_structured_output(self, _schema: Any) -> "_FakeMetaLLM":
        return self

    def with_config(self, _config: dict[str, Any]) -> "_FakeMetaLLM":
        return self

    async def ainvoke(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._response


class _MockPlannerWithMetaClassifier:
    planner_llm: Any | None

    def __init__(
        self,
        output: PlannerOutput,
        *,
        planner_llm: Any | None = None,
        classifier_result: Any | None = None,
    ) -> None:
        self._output = output
        self.planner_llm = planner_llm
        self.classifier_result = classifier_result
        self.classifier_calls = 0

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        return self._output

    async def interpret_meta_query(self, phone_number: str, text: str, context: str = "None") -> Any:
        del phone_number, text, context
        self.classifier_calls += 1
        if self.classifier_result is None:
            return {
                "is_meta_query": False,
                "meta_kind": "not_meta",
                "confidence": 0.2,
                "detected_language": "English",
                "reason": "default",
            }
        return self.classifier_result


def _apply(state: OrchestratorState, updates: dict[str, Any]) -> OrchestratorState:
    return state.model_copy(update=updates)


@pytest.mark.asyncio
async def test_planner_meta_response_key_skips_classifier() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.identity",
        confidence=0.9,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlannerWithMetaClassifier(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": "I am Narya AI."}),
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

    assert planner.classifier_calls == 0
    assert state.final_response == "I am Narya AI."


@pytest.mark.asyncio
async def test_planner_classifier_routes_creator_query_when_no_meta_key() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="Can I help?",
        response_key="conversational.checkin",
        confidence=0.7,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlannerWithMetaClassifier(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": "Narya AI was built by Fusepay."}),
        classifier_result={
            "is_meta_query": True,
            "meta_kind": "creator",
            "confidence": 0.93,
            "detected_language": "English",
            "reason": "creator ask",
        },
    )
    state = OrchestratorState(
        user_id="u_meta_r_2",
        phone_number="2348000001002",
        channel="whatsapp",
        last_message_text="who created you",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "services": {}, "redis_client": None}}

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert planner.classifier_calls == 1
    assert state.final_response == "Narya AI was built by Fusepay."


@pytest.mark.asyncio
async def test_planner_classifier_unknown_lore_returns_strict_polite_refusal() -> None:
    planner_output = PlannerOutput(
        primary_intent="faq",
        response="",
        response_key=None,
        confidence=0.8,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlannerWithMetaClassifier(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": "unused"}),
        classifier_result={
            "is_meta_query": True,
            "meta_kind": "unknown_self_lore",
            "confidence": 0.95,
            "detected_language": "English",
            "reason": "lore",
        },
    )
    state = OrchestratorState(
        user_id="u_meta_r_3",
        phone_number="2348000001003",
        channel="whatsapp",
        last_message_text="is it from lotr",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "services": {}, "redis_client": None}}

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert planner.classifier_calls == 1
    assert state.final_response == render_message("meta.unknown_self_lore_refusal", "en")


@pytest.mark.asyncio
async def test_planner_classifier_not_meta_keeps_existing_deterministic_response() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.checkin",
        confidence=0.9,
        detected_language="English",
        tasks=[],
    )
    planner = _MockPlannerWithMetaClassifier(
        planner_output,
        planner_llm=_FakeMetaLLM({"handoff": "meta", "language": "en", "message": "unused"}),
        classifier_result={
            "is_meta_query": False,
            "meta_kind": "not_meta",
            "confidence": 0.31,
            "detected_language": "English",
            "reason": "not meta",
        },
    )
    state = OrchestratorState(
        user_id="u_meta_r_4",
        phone_number="2348000001004",
        channel="whatsapp",
        last_message_text="how far",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "services": {}, "redis_client": None}}

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert planner.classifier_calls == 1
    assert state.final_response == render_message("conversational.checkin", "en")
