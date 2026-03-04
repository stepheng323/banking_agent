import json
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput


class _StaticPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self.output = output
        self.last_context: str | None = None

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text
        self.last_context = context
        return self.output


class _RedisWithLiveQuerySession:
    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return None
        if "query:session:" in key:
            return json.dumps(
                {
                    "session_active": True,
                    "current_page": 0,
                    "query_result": {"summary_text": "You spent ₦5,000 today."},
                    "query": {
                        "intent": "transaction_list",
                        "time_range": {"start": "2026-03-04", "end": "2026-03-04", "granularity": "day"},
                    },
                }
            )
        return None

    async def delete(self, key: str) -> int:
        del key
        return 0


class _RedisWithoutQuerySession:
    async def get(self, key: str) -> str | None:
        del key
        return None

    async def delete(self, key: str) -> int:
        del key
        return 0


@pytest.mark.asyncio
async def test_planner_stashes_live_query_session_when_switching_to_transfer() -> None:
    planner = _StaticPlanner(
        PlannerOutput(
            primary_intent="transfer",
            response="",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language=None,
            context_fastpath_subtype=None,
            normalized_instruction="send 5k to Ada",
            tasks=[
                PlannedTask(
                    task_id="task_1",
                    action="send_money",
                    executor="transfer",
                    instruction="send 5k to Ada",
                )
            ],
        )
    )
    state = OrchestratorState(
        user_id="u_stash_1",
        phone_number="2348000000200",
        channel="whatsapp",
        last_message_text="send 5k to Ada",
        loaded_context={"language": "en"},
        tasks={},
        waves=[],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "redis_client": _RedisWithLiveQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert "stashed_query_session" in updates
    stashed = updates["stashed_query_session"]
    assert isinstance(stashed, dict)
    assert stashed.get("session_active") is True
    assert isinstance(stashed.get("query_result"), dict)


@pytest.mark.asyncio
async def test_planner_uses_stashed_query_session_context_when_redis_session_missing() -> None:
    planner = _StaticPlanner(
        PlannerOutput(
            primary_intent="conversational",
            response="Noted.",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language=None,
            context_fastpath_subtype=None,
            normalized_instruction="any credits?",
            tasks=[],
        )
    )
    state = OrchestratorState(
        user_id="u_stash_2",
        phone_number="2348000000201",
        channel="whatsapp",
        last_message_text="any credits?",
        loaded_context={"language": "en"},
        tasks={},
        waves=[],
        current_wave_index=0,
        stashed_query_session={
            "session_active": True,
            "query_result": {"summary_text": "You spent ₦5,000 today."},
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "redis_client": _RedisWithoutQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["final_response"] == "Noted."
    assert planner.last_context is not None
    assert "Active Query Session" in planner.last_context
    assert "You spent ₦5,000 today." in planner.last_context
