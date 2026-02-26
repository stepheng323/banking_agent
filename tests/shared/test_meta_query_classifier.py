"""Tests for broad meta-query classifier routing contract."""

from __future__ import annotations

import json
from typing import Any

import pytest

from shared.services.task_planner import TaskPlanner


class _FakeStructuredRunner:
    def __init__(self, schema_name: str) -> None:
        self._schema_name = schema_name

    async def ainvoke(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if self._schema_name != "MetaQueryDecision":
            return {}

        user_prompt = messages[-1]["content"]
        message_text = ""
        marker = 'Message: """'
        if marker in user_prompt:
            tail = user_prompt.split(marker, 1)[1]
            message_text = tail.rsplit('"""', 1)[0].strip()

        lowered = message_text.lower()
        if any(term in lowered for term in {"who created you", "who build", "who made", "ta ya gina ka"}):
            return {
                "is_meta_query": True,
                "meta_kind": "creator",
                "confidence": 0.92,
                "detected_language": "English",
                "reason": "creator ask",
            }
        if any(term in lowered for term in {"what is your name", "who are you", "oruko re", "kedu onye"}):
            return {
                "is_meta_query": True,
                "meta_kind": "identity",
                "confidence": 0.9,
                "detected_language": "English",
                "reason": "identity ask",
            }
        if any(term in lowered for term in {"is it from lotr", "tolkien", "ring of fire"}):
            return {
                "is_meta_query": True,
                "meta_kind": "unknown_self_lore",
                "confidence": 0.95,
                "detected_language": "English",
                "reason": "lore ask",
            }
        if any(term in lowered for term in {"what can you do", "wetin you fit do", "menene zaka iya yi", "gini ka nwere ike ime"}):
            return {
                "is_meta_query": True,
                "meta_kind": "capabilities",
                "confidence": 0.9,
                "detected_language": "English",
                "reason": "capability ask",
            }
        return {
            "is_meta_query": False,
            "meta_kind": "not_meta",
            "confidence": 0.4,
            "detected_language": "English",
            "reason": "banking intent",
        }


class _FakePlannerLLM:
    def with_structured_output(self, schema: Any) -> _FakeStructuredRunner:
        name = getattr(schema, "__name__", "")
        return _FakeStructuredRunner(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_kind"),
    [
        ("what is your name", "identity"),
        ("who created you", "creator"),
        ("wetin you fit do", "capabilities"),
        ("menene zaka iya yi", "capabilities"),
        ("gini ka nwere ike ime", "capabilities"),
    ],
)
async def test_meta_query_classifier_detects_multilingual_self_queries(message: str, expected_kind: str) -> None:
    planner = TaskPlanner(planner_llm=_FakePlannerLLM())

    decision = await planner.interpret_meta_query("2348000000000", message, context=json.dumps({"scope": "test"}))

    assert decision.is_meta_query is True
    assert decision.meta_kind == expected_kind
    assert decision.confidence >= 0.75


@pytest.mark.asyncio
async def test_meta_query_classifier_detects_unknown_self_lore() -> None:
    planner = TaskPlanner(planner_llm=_FakePlannerLLM())

    decision = await planner.interpret_meta_query("2348000000000", "is it from lotr", context="None")

    assert decision.is_meta_query is True
    assert decision.meta_kind == "unknown_self_lore"


@pytest.mark.asyncio
async def test_meta_query_classifier_returns_not_meta_for_banking_command() -> None:
    planner = TaskPlanner(planner_llm=_FakePlannerLLM())

    decision = await planner.interpret_meta_query("2348000000000", "send 5k to tolu", context="None")

    assert decision.is_meta_query is False
    assert decision.meta_kind == "not_meta"
