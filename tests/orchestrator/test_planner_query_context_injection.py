from datetime import date

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerPlanResult
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from banking.transactions.query.grounding.frames import build_query_frame
from banking.transactions.query.models.domain import Filters, QueryIntent, QueryResult, TimeRange
from shared.types.planner import PlannerOutput
from tests.orchestrator.routing_fixtures import planner_test_result
from tests.query.factories import make_query_request


class _CapturingPlanner:
    def __init__(self) -> None:
        self.last_context: str | None = None

    async def plan_tasks_with_quality(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerPlanResult:
        del phone_number, text
        self.last_context = context
        output = PlannerOutput(
            primary_intent="conversational",
            response="Noted.",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language=None,
            normalized_instruction="",
            tasks=[],
        )
        return planner_test_result(output)


class _RedisWithoutQuerySession:
    async def get(self, key: str) -> str | None:
        del key
        return None

    async def delete(self, key: str) -> int:
        del key
        return 0


@pytest.mark.asyncio
async def test_planner_injects_filter_refinement_guidance_for_active_query_session() -> None:
    planner = _CapturingPlanner()
    query_request = make_query_request(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=Filters(transaction_type="debit"),
        time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 31)),
    )
    query_frame = build_query_frame(
        query_request=query_request,
        result=QueryResult(summary_text="Recent debit transactions", query_request=query_request),
        turn_index=1,
    )
    state = OrchestratorState(
        user_id="u_query_ctx_1",
        phone_number="2348000000100",
        channel="whatsapp",
        last_message_text="any credits?",
        loaded_context={"language": "en"},
        tasks={},
        waves=[],
        current_wave_index=0,
        context_frames=[
            ContextFrame(
                frame_id="query-surface",
                frame_type=ContextFrameType.TRANSACTION_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.TRANSACTION,
                        entity_id="txn-1",
                        label="Debit transaction",
                    )
                ],
                created_at_ts=1_771_000_000,
                ttl_seconds=600_000_000,
                metadata={
                    "source": "query",
                    "summary_text": "Recent debit transactions",
                    "query_frame": query_frame.model_dump(mode="json"),
                    "surface_mode": "transaction_list",
                },
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "semantic_router_llm": planner,
            "capability_classifier_llm": planner,
            "redis_client": _RedisWithoutQuerySession(),
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["final_response"] == "Noted."
    assert planner.last_context is not None
    assert "Active Query Session" in planner.last_context
    assert "Recent debit transactions" in planner.last_context
    assert "Continuation/refinement/fact questions" in planner.last_context
