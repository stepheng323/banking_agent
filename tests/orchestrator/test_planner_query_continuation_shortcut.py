import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerPlanResult
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from shared.types.planner import PlannerOutput, QueryTaskParameters, make_planned_task
from tests.orchestrator.routing_fixtures import planner_test_result


class _PlannerReturningQueryTask:
    def __init__(self) -> None:
        self.plan_calls = 0

    async def plan_tasks_with_quality(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerPlanResult:
        del phone_number, context, prompt_signals
        self.plan_calls += 1
        output = PlannerOutput(
            primary_intent="query",
            response="",
            confidence=0.9,
            normalized_instruction=text,
            tasks=[
                make_planned_task(
                    task_id="q1",
                    action="transaction_list",
                    executor="query",
                    instruction=text,
                    parameters=QueryTaskParameters(),
                    risk="READ_ONLY",
                )
            ],
        )
        return planner_test_result(output)


class _PlannerReturningDirectResponse:
    def __init__(self) -> None:
        self.plan_calls = 0

    async def plan_tasks_with_quality(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerPlanResult:
        del phone_number, text, context, prompt_signals
        self.plan_calls += 1
        output = PlannerOutput(
            primary_intent="conversational",
            response="Need planner",
            confidence=0.9,
            normalized_instruction="show me",
            tasks=[],
        )
        return planner_test_result(output)


class _RedisWithQuerySession:
    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return None
        return None

    async def delete(self, key: str) -> int:
        del key
        return 1


@pytest.mark.asyncio
async def test_planner_no_longer_shortcuts_obvious_query_continuation() -> None:
    planner = _PlannerReturningQueryTask()
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
            "task_planner": planner,
            "semantic_router_llm": planner,
            "capability_classifier_llm": planner,
            "redis_client": _RedisWithQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert planner.plan_calls == 1
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
            "semantic_router_llm": planner,
            "capability_classifier_llm": planner,
            "redis_client": _RedisWithQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert planner.plan_calls == 1
    assert updates["final_response"] == "Need planner"
