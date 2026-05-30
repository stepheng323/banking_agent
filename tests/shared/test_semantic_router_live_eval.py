"""Optional live-model evals for the semantic router."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from shared.types.planner import SemanticRouteDecision

_CASES_PATH = Path("tests/fixtures/semantic_router_live_cases.json")


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def _live_chat_model() -> Any:
    if os.getenv("SEMANTIC_ROUTER_LIVE_EVAL") != "1":
        pytest.skip("set SEMANTIC_ROUTER_LIVE_EVAL=1 to run live semantic-router evals")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for live semantic-router evals")

    chat_openai = pytest.importorskip("langchain_openai")
    model_name = os.getenv("SEMANTIC_ROUTER_LIVE_MODEL", os.getenv("PLANNER_REPLAY_PARITY_MODEL", "gpt-4o-mini"))
    return chat_openai.ChatOpenAI(model=model_name, temperature=0, seed=42)


def _assert_router_case(case: dict[str, Any], result: SemanticRouteDecision) -> None:
    assert result.decision == case["expected_decision"], case["id"]

    expected_target_intent = case.get("expected_target_intent")
    if expected_target_intent is not None:
        assert result.target_intent == expected_target_intent, case["id"]

    expected_response_key = case.get("expected_response_key")
    if expected_response_key is not None:
        assert result.response_key == expected_response_key, case["id"]

    allowed_modes = case.get("allowed_modes")
    if allowed_modes is not None:
        assert result.mode in allowed_modes, case["id"]

    expected_executors = case.get("expected_executors")
    if expected_executors is not None:
        assert result.expected_transaction_executors == expected_executors, case["id"]


@pytest.mark.asyncio
async def test_semantic_router_live_eval_cases() -> None:
    llm = _live_chat_model()
    planner = TaskPlanner(planner_llm=llm, interrupt_llm=llm)

    for case in _load_cases():
        result = await planner.route_semantic_turn(
            phone_number="2348011111111",
            text=case["text"],
            context=case["context"],
            path_label="live_eval",
        )

        assert isinstance(result, SemanticRouteDecision), case["id"]
        _assert_router_case(case, result)
