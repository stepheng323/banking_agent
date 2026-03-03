import json
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks


class _FailingPlanner:
    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> Any:
        del phone_number, text, context
        raise AssertionError("planner LLM should not run for deterministic query-continuation shortcut")


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
        last_message_text="show my transactions",
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
    assert task.payload["message"] == "show my transactions"
