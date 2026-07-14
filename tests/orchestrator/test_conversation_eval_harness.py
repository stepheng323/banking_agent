from __future__ import annotations

import time

import pytest

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from scripts.readiness_models import ReadinessExpectation, ReadinessScenario, ReadinessTurn
from shared.types.planner import SemanticRouteDecision
from tests.orchestrator.conversation_harness import (
    ConversationScenario,
    ConversationTurn,
    run_conversation_scenario,
    run_readiness_scenario_fast,
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


@pytest.mark.asyncio
async def test_fast_harness_accepts_canonical_readiness_scenario() -> None:
    planner = _CountingPlanner()
    scenario = ReadinessScenario(
        id="canonical-fast-eval",
        category="incomplete_input",
        turns=(
            ReadinessTurn(
                "send me",
                ReadinessExpectation(
                    expect_any=("send money", "recipient"),
                    expect_forbidden_task_types=("transfer", "airtime", "data"),
                    expect_no_money_movement=True,
                ),
            ),
        ),
    )

    result = await run_readiness_scenario_fast(
        scenario,
        initial_state=_state(user_id="u_eval_canonical_fast"),
        planner=planner,
    )

    assert (
        result.final_state.turn_directive.decision if result.final_state.turn_directive else None
    ) == "banking_coded_ambiguity_transfer"
    assert result.final_state.tasks == {}


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
                    expect_turn_owner="guardrail",
                    expect_turn_decision="banking_coded_ambiguity_transfer",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
                ConversationTurn(
                    user="send receipt for that",
                    expect_response_contains=("Which transaction", "check"),
                    expect_path_shape="banking_coded_ambiguity_clarify",
                    expect_turn_owner="guardrail",
                    expect_turn_decision="banking_coded_ambiguity_support",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
                ConversationTurn(
                    user="payment history",
                    expect_response_contains=("balance", "transaction"),
                    expect_path_shape="banking_coded_ambiguity_clarify",
                    expect_turn_owner="guardrail",
                    expect_turn_decision="banking_coded_ambiguity_account_query",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert (
        result.final_state.turn_directive.decision if result.final_state.turn_directive else None
    ) == "banking_coded_ambiguity_account_query"
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
                    expect_turn_owner="guardrail",
                    expect_turn_decision="deterministic_data_domain",
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
                    expect_turn_owner="guardrail",
                    expect_turn_decision="meta_direct",
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
                    expect_turn_owner="guardrail",
                    expect_turn_decision="resume_session_direct",
                    expect_task_types=("orchestrator",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert task.payload == {"action": "resume_session"}
    assert planner.route_calls == 0
