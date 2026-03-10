import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
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


@pytest.mark.asyncio
async def test_show_saved_beneficiaries_routes_to_list_beneficiaries_on_first_pass() -> None:
    planner_output = PlannerOutput(
        primary_intent="beneficiary",
        response="",
        response_key=None,
        confidence=0.93,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_fastpath_subtype=None,
        beneficiary_route="beneficiary_list",
        normalized_instruction="show saved beneficiaries",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="list_beneficiaries",
                executor="beneficiary",
                instruction="show saved beneficiaries",
                parameters=TaskParameters(),
                risk="READ_ONLY",
            )
        ],
    )
    state = OrchestratorState(
        user_id="u_benef_route_1",
        phone_number="2348111222333",
        channel="whatsapp",
        last_message_text="Show saved beneficiaries",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
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
        beneficiary_route="recipient_ranking",
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
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    task = updates["tasks"]["t1"]
    assert task.type == "query"
    assert task.payload.get("action") == "beneficiary_summary"


@pytest.mark.asyncio
async def test_invalid_beneficiary_route_contract_returns_clarify_instead_of_rewrite() -> None:
    planner_output = PlannerOutput(
        primary_intent="query",
        response="",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_fastpath_subtype=None,
        beneficiary_route="beneficiary_list",
        normalized_instruction="show saved beneficiaries",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="beneficiary_summary",
                executor="query",
                instruction="show saved beneficiaries",
                parameters=TaskParameters(),
                risk="READ_ONLY",
            )
        ],
    )
    state = OrchestratorState(
        user_id="u_benef_route_3",
        phone_number="2348111222555",
        channel="whatsapp",
        last_message_text="Show saved beneficiaries",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert "tasks" not in updates
    assert updates.get("final_response")


@pytest.mark.asyncio
async def test_save_beneficiary_without_suggestion_context_returns_clarify() -> None:
    planner_output = PlannerOutput(
        primary_intent="beneficiary",
        response="",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_fastpath_subtype=None,
        beneficiary_route="none",
        normalized_instruction="save as mum",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="save_beneficiary",
                executor="beneficiary",
                instruction="save as mum",
                parameters=TaskParameters(),
                risk="READ_ONLY",
            )
        ],
    )
    state = OrchestratorState(
        user_id="u_benef_route_4",
        phone_number="2348111222666",
        channel="whatsapp",
        last_message_text="save as mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert "tasks" not in updates
    assert updates.get("final_response")
