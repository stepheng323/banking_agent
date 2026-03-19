import json
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks


class _FailingPlanner:
    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> Any:
        del phone_number, text, context
        raise AssertionError("planner LLM should not run for deterministic query-continuation shortcut")


class _PlannerReturningDirectResponse:
    def __init__(self) -> None:
        self.plan_calls = 0

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> Any:
        del phone_number, text, context, prompt_signals
        self.plan_calls += 1
        return type(
            "PlannerOutput",
            (),
            {
                "primary_intent": "conversational",
                "response": "Need planner",
                "response_key": None,
                "confidence": 0.9,
                "is_complex": False,
                "is_cancellation": False,
                "is_confirmation": False,
                "detected_language": None,
                "context_fastpath_subtype": None,
                "normalized_instruction": "show me",
                "tasks": [],
            },
        )()


class _RedisWithQuerySession:
    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return None
        if ":query:session:" in key:
            return None
        if "query:session:" in key:
            return json.dumps(
                {
                    "session_active": True,
                    "query_result": {"summary_text": "You spent ₦5,000 today."},
                }
            )
        return None


@pytest.mark.asyncio
async def test_planner_shortcuts_obvious_query_continuation() -> None:
    state = OrchestratorState(
        user_id="u_shortcut_1",
        phone_number="2348000000002",
        channel="whatsapp",
        last_message_text="more",
        loaded_context={"language": "en"},
        tasks={},
        waves=[],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FailingPlanner(),
            "redis_client": _RedisWithQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert "tasks" in updates
    assert "waves" in updates
    task = next(iter(updates["tasks"].values()))
    assert task.type == "query"
    assert task.payload["message"] == "more"


@pytest.mark.asyncio
async def test_planner_does_not_shortcut_semantic_query_followup() -> None:
    planner = _PlannerReturningDirectResponse()
    state = OrchestratorState(
        user_id="u_shortcut_2",
        phone_number="2348000000003",
        channel="whatsapp",
        last_message_text="show me",
        loaded_context={"language": "en"},
        tasks={},
        waves=[],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "redis_client": _RedisWithQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert planner.plan_calls == 1
    assert updates["final_response"] == "Need planner"
