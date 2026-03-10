from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    planner_llm: Any | None = None

    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
    ) -> PlannerOutput:
        del phone_number, text, context, prompt_signals
        return self._output


class _RouteStructured:
    def __init__(self, route: str, confidence: float = 0.95) -> None:
        self._route = route
        self._confidence = confidence

    async def ainvoke(self, prompt: object) -> dict[str, Any]:
        del prompt
        return {"route": self._route, "confidence": self._confidence}


class _RouteLLM:
    def __init__(self, route: str, confidence: float = 0.95) -> None:
        self._route = route
        self._confidence = confidence

    def with_structured_output(self, schema: object) -> _RouteStructured:
        del schema
        return _RouteStructured(self._route, self._confidence)


class _RoutingPlanner(_MockPlanner):
    def __init__(self, output: PlannerOutput, route: str, confidence: float = 0.95) -> None:
        super().__init__(output)
        self.planner_llm = _RouteLLM(route, confidence)


@pytest.mark.asyncio
async def test_show_beneficiaries_rewrites_query_beneficiary_summary_to_beneficiary_list_task() -> None:
    planner_output = PlannerOutput(
        primary_intent="query",
        response="",
        response_key=None,
        confidence=0.93,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_fastpath_subtype=None,
        normalized_instruction="show my beneficiaries",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="beneficiary_summary",
                executor="query",
                instruction="show my beneficiaries",
                parameters=TaskParameters(),
                risk="READ_ONLY",
            )
        ],
    )
    state = OrchestratorState(
        user_id="u_benef_route_1",
        phone_number="2348111222333",
        channel="whatsapp",
        last_message_text="Show my beneficiaries",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _RoutingPlanner(planner_output, route="beneficiary_list"),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    task = updates["tasks"]["t1"]
    assert task.type == "beneficiary"
    assert task.payload.get("action") == "list_beneficiaries"


@pytest.mark.asyncio
async def test_top_recipients_query_keeps_query_beneficiary_summary_task() -> None:
    planner_output = PlannerOutput(
        primary_intent="query",
        response="",
        response_key=None,
        confidence=0.93,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_fastpath_subtype=None,
        normalized_instruction="who did i send money to the most this month",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="beneficiary_summary",
                executor="query",
                instruction="who did i send money to the most this month",
                parameters=TaskParameters(),
                risk="READ_ONLY",
            )
        ],
    )
    state = OrchestratorState(
        user_id="u_benef_route_2",
        phone_number="2348111222444",
        channel="whatsapp",
        last_message_text="Who did I send money to the most this month?",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _RoutingPlanner(planner_output, route="recipient_ranking"),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    task = updates["tasks"]["t1"]
    assert task.type == "query"
    assert task.payload.get("action") == "beneficiary_summary"
