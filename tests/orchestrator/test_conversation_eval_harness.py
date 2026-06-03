from __future__ import annotations

import time

import pytest

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.types.planner import SemanticRouteDecision
from tests.orchestrator.conversation_harness import (
    ConversationScenario,
    ConversationTurn,
    run_conversation_scenario,
)


class _CountingPlanner:
    def __init__(self) -> None:
        self.route_calls = 0

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.route_calls += 1
        return SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.91,
            response_key="conversational.clarify",
            reason="conversation harness fallback route",
        )


def _state(*, user_id: str, text: str = "") -> OrchestratorState:
    return OrchestratorState(
        user_id=user_id,
        phone_number=f"2348000{user_id[-6:]}",
        channel="whatsapp",
        last_message_text=text,
        loaded_context={"language": "en"},
    )


def _resume_prompt_frame() -> ContextFrame:
    return ContextFrame(
        frame_id="resume-frame-eval",
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_id="resume-prompt",
                entity_type=EntityType.GENERIC,
                label="Resume transfer",
                data={"resume_prompt": True, "stash_id": "stash-eval"},
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_conversation_eval_banking_coded_ambiguity_prompts_skip_router() -> None:
    planner = _CountingPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="banking_coded_ambiguity_prompts",
            initial_state=_state(user_id="u_eval_ambiguity_1"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="send me",
                    expect_response_contains=("send money", "recipient"),
                    expect_path_shape="banking_coded_ambiguity_clarify",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="banking_coded_ambiguity_transfer",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
                ConversationTurn(
                    user="send receipt for that",
                    expect_response_contains=("Which transaction", "check"),
                    expect_path_shape="banking_coded_ambiguity_clarify",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="banking_coded_ambiguity_support",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
                ConversationTurn(
                    user="payment history",
                    expect_response_contains=("balance", "transaction"),
                    expect_path_shape="banking_coded_ambiguity_clarify",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="banking_coded_ambiguity_account_query",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert result.final_state.routing_decision == "banking_coded_ambiguity_account_query"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_conversation_eval_self_data_request_routes_directly() -> None:
    planner = _CountingPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="self_data_request_direct",
            initial_state=_state(user_id="u_eval_self_data_1").model_copy(update={"phone_number": "2348162511023"}),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="buy me data",
                    expect_path_shape="deterministic_data_domain",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="deterministic_data_domain",
                    expect_task_types=("data",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = result.final_state.tasks["direct_data"]
    assert task.payload["target_phone"] == "08162511023"
    assert task.payload["is_self"] is True
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_conversation_eval_wrong_name_stays_meta_direct() -> None:
    planner = _CountingPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="wrong_name_meta_direct",
            initial_state=_state(user_id="u_eval_wrong_name"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="Hi Xara",
                    expect_response_contains=("Not Xara", "I'm"),
                    expect_path_shape="meta_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="meta_direct",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert result.final_state.final_response is not None
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_conversation_eval_resume_prompt_continue_routes_to_orchestrator_task() -> None:
    planner = _CountingPlanner()
    state = _state(user_id="u_eval_resume")
    state = state.model_copy(
        update={
            "context_frames": [_resume_prompt_frame()],
            "stashed_sessions": [
                {
                    "stash_id": "stash-eval",
                    "intent": "transfer",
                    "tasks": {},
                    "waves": [],
                    "current_wave_index": 0,
                }
            ],
        }
    )

    result = await run_conversation_scenario(
        ConversationScenario(
            id="resume_prompt_continue",
            initial_state=state,
            planner=planner,
            turns=(
                ConversationTurn(
                    user="continue",
                    expect_path_shape="resume_session_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="resume_session_direct",
                    expect_task_types=("orchestrator",),
                    expect_planner_route_calls_delta=0,
                    expect_state={"direct_path_triggered": True},
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert task.payload == {"action": "resume_session"}
    assert planner.route_calls == 0
