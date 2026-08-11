import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerPlanResult
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from shared.types.planner import (
    BeneficiaryTaskParameters,
    PlannerOutput,
    QueryTaskParameters,
    make_planned_task,
)
from tests.orchestrator.routing_fixtures import planner_test_result


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

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
        return planner_test_result(self._output)


class _RedisWithSuggestionOnly:
    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return '{"recipient_name":"Tolu Adedayo","recipient_account":"0760505261","bank_name":"First Bank"}'
        return None

    async def delete(self, key: str) -> int:
        del key
        return 1


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
        beneficiary_route="beneficiary_list",
        normalized_instruction="show saved beneficiaries",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="list_beneficiaries",
                executor="beneficiary",
                instruction="show saved beneficiaries",
                parameters=BeneficiaryTaskParameters(),
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
        beneficiary_route="recipient_ranking",
        normalized_instruction="who did i send money to the most this month",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="beneficiary_summary",
                executor="query",
                instruction="who did i send money to the most this month",
                parameters=QueryTaskParameters(),
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
        beneficiary_route="beneficiary_list",
        normalized_instruction="show saved beneficiaries",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="beneficiary_summary",
                executor="query",
                instruction="show saved beneficiaries",
                parameters=QueryTaskParameters(),
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
        beneficiary_route="none",
        normalized_instruction="save as mum",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="save_beneficiary",
                executor="beneficiary",
                instruction="save as mum",
                parameters=BeneficiaryTaskParameters(),
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


@pytest.mark.asyncio
async def test_planner_originated_save_beneficiary_is_rejected_even_with_pending_suggestion() -> None:
    planner_output = PlannerOutput(
        primary_intent="beneficiary",
        response="",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        beneficiary_route="none",
        normalized_instruction="save as mum",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="save_beneficiary",
                executor="beneficiary",
                instruction="save as mum",
                parameters=BeneficiaryTaskParameters(alias="mum"),
                risk="READ_ONLY",
            )
        ],
    )
    state = OrchestratorState(
        user_id="u_benef_route_5",
        phone_number="2348111222777",
        channel="whatsapp",
        last_message_text="save as mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": _RedisWithSuggestionOnly(),
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert "tasks" not in updates
    assert updates.get("final_response")
