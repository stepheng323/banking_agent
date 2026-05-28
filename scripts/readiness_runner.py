"""Hybrid readiness runner for scripted banking-agent transcripts."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Allow direct execution via scripts that import this module.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import scripts.readiness_assertions as readiness_assertions
import scripts.readiness_rendering as readiness_rendering
import scripts.readiness_report as readiness_report
import scripts.readiness_sequence as readiness_sequence
from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.node import session_gate_direct_path
from apps.chat.src.runtime.chat_worker_dependencies import _build_orchestrator_runtime_bundle, _resolve_role_model
from scripts.readiness_models import (
    ReadinessInvocation,
    ReadinessMode,
    ReadinessRunResult,
    ReadinessScenario,
    ReadinessScenarioName,
    ReadinessTurn,
)
from scripts.readiness_scenarios import resolve_scenarios
from scripts.seed_user_test_data import _resolve_target_user, _seed_for_user
from shared.assistant_profile.loader import get_cached_assistant_profile
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.guardrails.loader import get_cached_guardrails
from shared.i18n.renderer import validate_catalog_completeness
from shared.policy.loader import get_cached_policy
from shared.policy.validation import validate_policy_coverage
from shared.repositories.user_repository import UserRepository
from shared.services.task_planner_prompt_runtime import refresh_runtime_planner_system_prompt
from shared.services.unsupported_capability_models import UnsupportedBoundaryTurnOutput
from shared.types.planner import SemanticRouteDecision


class NoopPublisher:
    """Capture async jobs without publishing them to Redis/SQS."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.messages.append({"topic": topic, "message": message})


class DeterministicReadinessPlanner:
    """Bounded fake planner for CI-safe direct-path readiness scenarios."""

    def __init__(self) -> None:
        self.route_calls = 0
        self.boundary_calls = 0
        self.schedule_read_calls = 0

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.route_calls += 1
        return SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.2,
            response_key="conversational.clarify",
            response="Tell me what banking task you want to do next.",
            expected_transaction_executors=[],
            reason="deterministic readiness fallback",
        )

    async def classify_unsupported_boundary_turn(
        self,
        *args: object,
        **kwargs: object,
    ) -> UnsupportedBoundaryTurnOutput:
        del args, kwargs
        self.boundary_calls += 1
        return UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="lending",
            confidence=0.91,
            reason="deterministic readiness unsupported continuation",
        )

    async def route_schedule_read_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.schedule_read_calls += 1
        return SemanticRouteDecision(
            decision="domain_schedule",
            target_intent="schedule",
            mode="new",
            confidence=0.91,
            schedule_response_mode="list",
            expected_transaction_executors=[],
            reason="deterministic readiness schedule read",
        )


def _route_metadata_from_state(state: OrchestratorState) -> dict[str, Any]:
    return {
        "semantic_path_shape": state.semantic_path_shape,
        "routing_owner": state.routing_owner,
        "routing_decision": state.routing_decision,
        "routing_target_domain": state.routing_target_domain,
        "routing_mode": state.routing_mode,
        "route_source": state.route_source,
    }


def _base_deterministic_state(*, scenario_id: str) -> OrchestratorState:
    state = OrchestratorState(
        user_id=f"readiness_{scenario_id}",
        phone_number="2348162511023",
        channel="whatsapp",
        loaded_context={"language": "en"},
    )
    if scenario_id == "unsupported":
        state = state.model_copy(
            update={
                "capability_boundary": None,
                "context_frames": [
                    ContextFrame(
                        frame_id="readiness-stale-transfer",
                        frame_type=ContextFrameType.GENERIC,
                        items=[
                            ContextEntity(
                                entity_id="stale-transfer",
                                entity_type=EntityType.GENERIC,
                                label="Stale transfer",
                                data={"summary": "₦2,000 transfer to Tolu Adebayo"},
                            )
                        ],
                        created_at_ts=int(time.time()),
                    )
                ],
            }
        )
    return state


async def run_deterministic_readiness(
    *,
    scenario_name: ReadinessScenarioName,
    stop_on_fail: bool = False,
) -> ReadinessRunResult:
    scenarios = resolve_scenarios(scenario_name)
    planner = DeterministicReadinessPlanner()
    states = {scenario.id: _base_deterministic_state(scenario_id=scenario.id) for scenario in scenarios}

    async def invoke_turn(scenario: ReadinessScenario, turn: ReadinessTurn, index: int) -> ReadinessInvocation:
        del index
        state = states[scenario.id]
        per_turn_reset = {
            "final_response": None,
            "policy_notice": None,
            "direct_path_triggered": False,
            "semantic_path_shape": None,
            "routing_owner": None,
            "routing_decision": None,
            "routing_target_domain": None,
            "routing_mode": None,
            "route_source": None,
            "routing_heuristic_type": None,
            "routing_heuristic_name": None,
            "planner_used": False,
            "suppress_empty_fallback": False,
        }
        state = state.model_copy(update={**per_turn_reset, "last_message_text": turn.text})
        updates = await session_gate_direct_path(
            state,
            {
                "configurable": {
                    "task_planner": planner,
                },
                "recursion_limit": 50,
            },
        )
        state = state.model_copy(update=updates)
        states[scenario.id] = state
        return ReadinessInvocation(
            response={"outbox": state.outbox, "text": state.final_response},
            route_metadata=_route_metadata_from_state(state),
            task_types=tuple(task.type for task in state.tasks.values()),
        )

    return await readiness_sequence.run_readiness_sequence(
        mode="deterministic",
        scenarios=scenarios,
        invoke_turn=invoke_turn,
        stop_on_fail=stop_on_fail,
        enforce_route_expectations=True,
    )


async def reset_redis_session(*, redis_client: Any, phone: str, channel: str) -> int:
    patterns = [
        f"checkpoint:{channel}:{phone}:*",
        f"checkpoint_write:{channel}:{phone}:*",
        f"write_keys_zset:{channel}:{phone}:*",
        f"user:{phone}:chat_history",
        f"query:session:{phone}",
        f"context_frames:{phone}",
    ]
    deleted = 0
    for pattern in patterns:
        keys = [key async for key in redis_client.scan_iter(match=pattern)]
        if keys:
            deleted += int(await redis_client.delete(*keys))
    return deleted


def _build_chat_model(*, role: str, model: str, timeout: float) -> Any:
    from langchain_openai import ChatOpenAI

    print(f"[setup] {role} model: {model}")
    return ChatOpenAI(model=model, temperature=0, timeout=timeout, max_retries=1)


async def build_dry_run_agent() -> tuple[UserRepository, OrchestratorAgent, NoopPublisher, Any]:
    validate_catalog_completeness()
    capability_policy = get_cached_policy(force_reload=True)
    validate_policy_coverage(capability_policy)
    get_cached_assistant_profile(force_reload=True)
    get_cached_guardrails(force_reload=True)
    refresh_runtime_planner_system_prompt()

    shared_redis = RedisClient.get_client()
    publisher = NoopPublisher()
    planner_llm = _build_chat_model(role="planner", model=settings.planner_model, timeout=30.0)
    app_env = settings.runtime.app_env
    query_model = _resolve_role_model(
        role="query",
        configured_model=settings.query_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    semantic_router_model = _resolve_role_model(
        role="semantic_router",
        configured_model=settings.semantic_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    interrupt_model = _resolve_role_model(
        role="interrupt_router",
        configured_model=settings.interrupt_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    extractor_model = _resolve_role_model(
        role="extractor",
        configured_model=settings.extractor_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )

    user_repo, _onboarding_executor, agent = _build_orchestrator_runtime_bundle(
        queue_publisher=publisher,
        messaging_clients={},
        shared_redis=shared_redis,
        llm=planner_llm,
        query_llm=_build_chat_model(role="query", model=query_model, timeout=30.0),
        semantic_router_llm=_build_chat_model(role="semantic_router", model=semantic_router_model, timeout=15.0),
        interrupt_llm=_build_chat_model(role="interrupt_router", model=interrupt_model, timeout=15.0),
        extractor_llm=_build_chat_model(role="extractor", model=extractor_model, timeout=20.0),
    )

    async def _noop_progress_updates(**_: Any) -> None:
        return None

    agent.orchestrator_handler._run_progress_updates = _noop_progress_updates
    return user_repo, agent, publisher, shared_redis


async def run_dry_run_readiness(
    *,
    scenario_name: ReadinessScenarioName,
    phone: str,
    channel: str = "telegram",
    channel_user_id: str | None = None,
    seed: bool = False,
    reset_session: bool = False,
    stop_on_fail: bool = False,
) -> ReadinessRunResult:
    target_user = await _resolve_target_user(phone)
    if seed:
        await _seed_for_user(target_user)

    user_repo, agent, publisher, redis_client = await build_dry_run_agent()
    user = await user_repo.get_by_phone(target_user.phone_number)
    if user is None:
        raise RuntimeError(f"Could not load user {target_user.phone_number}")

    scenarios = resolve_scenarios(scenario_name)
    run_id = uuid.uuid4().hex[:8]
    reset_scenarios: set[str] = set()

    async def before_scenario(scenario: ReadinessScenario) -> None:
        if not reset_session or scenario.id in reset_scenarios:
            return
        deleted = await reset_redis_session(redis_client=redis_client, phone=target_user.phone_number, channel=channel)
        print(f"[setup] reset Redis session keys before {scenario.id}: {deleted}")
        reset_scenarios.add(scenario.id)

    async def invoke_turn(scenario: ReadinessScenario, turn: ReadinessTurn, index: int) -> ReadinessInvocation:
        before_jobs = len(publisher.messages)
        message_id = f"readiness-{run_id}-{scenario.id}-{index}-{int(time.time() * 1000)}"
        response = await agent.invoke(
            phone_number=target_user.phone_number,
            text=turn.text,
            message_id=message_id,
            channel=channel,
            channel_identity=channel_user_id,
            user=user,
        )
        if turn.pin_after:
            pin_response = await agent.resume_transaction(
                phone_number=target_user.phone_number,
                flow_type=turn.pin_flow_type,
                pin_verified=True,
                channel=channel,
            )
            response = {
                **response,
                "outbox": [*(response.get("outbox") or []), *(pin_response.get("outbox") or [])],
                "intents": [*(response.get("intents") or []), *(pin_response.get("intents") or [])],
                "text": "\n\n".join(
                    part
                    for part in (
                        readiness_rendering.stringify_payload(response.get("text")),
                        readiness_rendering.stringify_payload(pin_response.get("text")),
                    )
                    if part
                ),
            }
        async_jobs = tuple(publisher.messages[before_jobs:])
        route_metadata = {
            key: response.get(key)
            for key in (
                "semantic_path_shape",
                "routing_owner",
                "routing_decision",
                "routing_target_domain",
                "routing_mode",
                "route_source",
            )
            if key in response
        }
        return ReadinessInvocation(
            response=response,
            route_metadata=route_metadata,
            task_types=readiness_assertions.task_types_from_response(response),
            async_jobs=async_jobs,
        )

    return await readiness_sequence.run_readiness_sequence(
        mode="dry-run",
        scenarios=scenarios,
        invoke_turn=invoke_turn,
        before_scenario=before_scenario,
        stop_on_fail=stop_on_fail,
        enforce_route_expectations=False,
    )


async def run_readiness(
    *,
    mode: ReadinessMode,
    scenario: ReadinessScenarioName,
    phone: str | None = None,
    channel: str = "telegram",
    channel_user_id: str | None = None,
    seed: bool = False,
    reset_session: bool = False,
    stop_on_fail: bool = False,
) -> ReadinessRunResult:
    if mode == "deterministic":
        return await run_deterministic_readiness(scenario_name=scenario, stop_on_fail=stop_on_fail)
    if not phone:
        raise ValueError("--phone is required in dry-run mode")
    return await run_dry_run_readiness(
        scenario_name=scenario,
        phone=phone,
        channel=channel,
        channel_user_id=channel_user_id,
        seed=seed,
        reset_session=reset_session,
        stop_on_fail=stop_on_fail,
    )


def run_readiness_sync(
    *,
    mode: ReadinessMode,
    scenario: ReadinessScenarioName,
    phone: str | None = None,
    channel: str = "telegram",
    channel_user_id: str | None = None,
    seed: bool = False,
    reset_session: bool = False,
    stop_on_fail: bool = False,
    json_output: str | None = None,
) -> int:
    result = asyncio.run(
        run_readiness(
            mode=mode,
            scenario=scenario,
            phone=phone,
            channel=channel,
            channel_user_id=channel_user_id,
            seed=seed,
            reset_session=reset_session,
            stop_on_fail=stop_on_fail,
        )
    )
    readiness_report.print_readiness_report(result)
    if json_output:
        readiness_report.write_json_report(result, json_output)
        print(f"[report] wrote {json_output}")
    return 0 if result.passed else 1
