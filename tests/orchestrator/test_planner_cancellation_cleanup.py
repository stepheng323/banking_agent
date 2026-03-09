"""Planner cancellation response cleanup tests."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import ActiveSession, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannerOutput


class _CancelPlanner:
    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
    ) -> PlannerOutput:
        del phone_number, text, context, prompt_signals
        return PlannerOutput(
            primary_intent="cancel",
            response="",
            response_key="planner.cancelled",
            confidence=0.99,
            is_complex=False,
            is_cancellation=True,
            is_confirmation=False,
            detected_language="English",
            context_fastpath_subtype=None,
            normalized_instruction="cancel",
            tasks=[],
        )


class _TrackingRedis:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> str | None:
        del key
        return None

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1


@pytest.mark.asyncio
async def test_planner_cancel_clears_query_and_task_state() -> None:
    redis_client = _TrackingRedis()
    state = OrchestratorState(
        user_id="u_cancel_cleanup_1",
        phone_number="2348000000301",
        channel="whatsapp",
        last_message_text="abort",
        loaded_context={"language": "en"},
        tasks={"t1": TaskSpec(id="t1", type="query", stage=TaskStage.DRAFT, payload={"message": "more"})},
        waves=[],
        current_wave_index=0,
        pending_interrupt=None,
        session_stack=[
            ActiveSession(domain="account", state="RUNNING", interrupt_policy="ALLOW"),
            ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW"),
        ],
        active_domain="query",
        stashed_query_session={"session_active": True},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _CancelPlanner(),
            "redis_client": redis_client,
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["final_response"] == "Cancelled."
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0
    assert updates["pending_interrupt"] is None
    assert len(updates["session_stack"]) == 1
    assert updates["session_stack"][0].domain == "account"
    assert updates["active_domain"] == "account"
    assert updates["stashed_query_session"] is None
    assert redis_client.deleted_keys == ["query:session:2348000000301"]
