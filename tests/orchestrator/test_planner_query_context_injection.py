import json

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from shared.types.planner import PlannerOutput


class _CapturingPlanner:
    def __init__(self) -> None:
        self.last_context: str | None = None

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None, path_label: str = "planner_path") -> PlannerOutput:
        del phone_number, text
        self.last_context = context
        return PlannerOutput(
            primary_intent="conversational",
            response="Noted.",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language=None,
            context_read_subtype=None,
            normalized_instruction="",
            tasks=[],
        )


class _RedisWithQuerySession:
    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return None
        if "query:session:" in key:
            return json.dumps(
                {
                    "session_active": True,
                    "query_result": {"summary_text": "You spent ₦5,000 today."},
                }
            )
        return None

    async def delete(self, key: str) -> int:
        del key
        return 0


@pytest.mark.asyncio
async def test_planner_injects_filter_refinement_guidance_for_active_query_session() -> None:
    planner = _CapturingPlanner()
    state = OrchestratorState(
        user_id="u_query_ctx_1",
        phone_number="2348000000100",
        channel="whatsapp",
        last_message_text="any credits?",
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

    assert updates["final_response"] == "Noted."
    assert planner.last_context is not None
    assert "any credits?" in planner.last_context
    assert "NOT conversational questions" in planner.last_context
    assert "Always route them as q" in planner.last_context
