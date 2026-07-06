import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from shared.types.planner import PlannerOutput


class _CapturingPlanner:
    def __init__(self) -> None:
        self.last_context: str | None = None

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
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
                    "query_contract": {
                        "intent": "transaction_list",
                        "time_start": "2026-03-01",
                        "time_end": "2026-03-31",
                        "timezone": "Africa/Lagos",
                        "filters": {"transaction_type": "debit"},
                    },
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
