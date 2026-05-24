from __future__ import annotations

import time

import pytest

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary, OrchestratorState
from shared.services.unsupported_capabilities import UnsupportedBoundaryTurnOutput
from shared.types.planner import SemanticRouteDecision
from tests.orchestrator.conversation_harness import (
    ConversationScenario,
    ConversationTurn,
    run_conversation_scenario,
)


class _AuditPlanner:
    def __init__(
        self,
        *,
        route_decision: SemanticRouteDecision | None = None,
        boundary_decision: UnsupportedBoundaryTurnOutput | None = None,
    ) -> None:
        self.route_decision = route_decision or SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.91,
            response="semantic router fallback",
            expected_transaction_executors=[],
            reason="audit fallback route",
        )
        self.boundary_decision = boundary_decision
        self.route_calls = 0
        self.boundary_calls = 0

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.route_calls += 1
        return self.route_decision

    async def classify_unsupported_boundary_turn(
        self, *args: object, **kwargs: object
    ) -> UnsupportedBoundaryTurnOutput:
        del args, kwargs
        self.boundary_calls += 1
        return self.boundary_decision or UnsupportedBoundaryTurnOutput(
            action="unclear",
            capability_key=None,
            confidence=0.2,
            reason="no audit boundary decision configured",
        )


def _state(
    *,
    user_id: str,
    text: str = "",
    capability_boundary: CapabilityBoundary | None = None,
    context_frames: list[ContextFrame] | None = None,
    stashed_sessions: list[dict[str, object]] | None = None,
) -> OrchestratorState:
    return OrchestratorState(
        user_id=user_id,
        phone_number=f"2348001{user_id[-6:]}",
        channel="whatsapp",
        last_message_text=text,
        loaded_context={"language": "en"},
        capability_boundary=capability_boundary,
        context_frames=context_frames or [],
        stashed_sessions=stashed_sessions or [],
    )


def _stale_transfer_frame() -> ContextFrame:
    return ContextFrame(
        frame_id="audit-stale-transfer",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-audit-stale",
                label="2,000 transfer to Tolu Adebayo",
                data={"task_type": "transfer", "amount": 2000, "recipient_name": "Tolu Adebayo"},
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )


def _resume_prompt_frame() -> ContextFrame:
    return ContextFrame(
        frame_id="audit-resume-prompt",
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_id="resume-prompt",
                entity_type=EntityType.GENERIC,
                label="Resume transfer",
                data={"resume_prompt": True, "stash_id": "stash-audit"},
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_quality_audit_transfer_start_is_deterministic_and_bypasses_router() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_transfer_start_fastpath",
            initial_state=_state(user_id="u_audit_transfer"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="send 5k to Ada",
                    expect_path_shape="deterministic_transfer_domain",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="fresh_transfer_command",
                    expect_task_types=("transfer",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "transfer"
    assert task.payload["message"] == "send 5k to Ada"
    assert task.payload["instruction"] == "send 5k to Ada"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_start_is_deterministic_and_bypasses_router() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_data_start_fastpath",
            initial_state=_state(user_id="u_audit_data"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="buy 1gb data for me",
                    expect_path_shape="deterministic_data_domain",
                    expect_routing_owner="guardrail",
                    expect_task_types=("data",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "data"
    assert task.payload["message"] == "buy 1gb data for me"
    assert task.payload["instruction"] == "buy 1gb data for me"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_mixed_transfer_and_crypto_keeps_transfer_with_policy_notice() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_mixed_transfer_unsupported",
            initial_state=_state(user_id="u_audit_mixed_transfer"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="send 5k to Ada and buy bitcoin for me",
                    expect_path_shape="mixed_capability_supported_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="mixed_supported_unsupported",
                    expect_task_types=("transfer",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "transfer"
    assert result.final_state.policy_notice is not None
    assert "money transfer" in result.final_state.policy_notice
    assert "investments or crypto" in result.final_state.policy_notice
    assert result.final_state.capability_boundary is None
    assert task.payload["message"] == "send 5k to Ada"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_unsupported_followup_does_not_reuse_stale_transaction_context() -> None:
    planner = _AuditPlanner(
        boundary_decision=UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="lending",
            confidence=0.91,
            reason="repayment promise continues lending request",
        )
    )

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_unsupported_boundary_no_stale_context",
            initial_state=_state(
                user_id="u_audit_unsupported",
                context_frames=[_stale_transfer_frame()],
            ),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="Can you borrow me money?",
                    expect_response_contains=("can't help with loans",),
                    expect_response_not_contains=("Tolu",),
                    expect_path_shape="meta_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="meta_direct",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
                ConversationTurn(
                    user="I will pay back",
                    expect_response_contains=("can't help with loans",),
                    expect_response_not_contains=("Tolu", "transfer to"),
                    expect_path_shape="capability_boundary_followup",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="unsupported_capability_followup",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert planner.boundary_calls == 1
    assert planner.route_calls == 0
    assert result.final_state.capability_boundary is not None
    assert result.final_state.capability_boundary.key == "lending"
    assert result.final_state.capability_boundary.followup_count == 1


@pytest.mark.asyncio
async def test_quality_audit_supported_data_request_clears_unsupported_boundary() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_supported_breakout_from_boundary",
            initial_state=_state(
                user_id="u_audit_boundary_breakout",
                capability_boundary=CapabilityBoundary(key="investments", label="investments or crypto"),
            ),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="buy 1gb data for me",
                    expect_path_shape="deterministic_data_domain",
                    expect_routing_owner="guardrail",
                    expect_task_types=("data",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert result.final_state.capability_boundary is None
    assert result.final_state.routing_target_domain == "data"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_resume_prompt_accepts_polite_approval() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_resume_polite_approval",
            initial_state=_state(
                user_id="u_audit_resume",
                context_frames=[_resume_prompt_frame()],
                stashed_sessions=[
                    {
                        "stash_id": "stash-audit",
                        "intent": "transfer",
                        "tasks": {},
                        "waves": [],
                        "current_wave_index": 0,
                    }
                ],
            ),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="Yes please",
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
