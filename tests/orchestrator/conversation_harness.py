from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.core.node import session_gate_direct_path
from scripts.readiness_models import ReadinessCriticality, ReadinessExpectation, ReadinessScenario


@dataclass(frozen=True)
class ConversationTurn:
    user: str
    expect_response_contains: tuple[str, ...] = ()
    expect_response_not_contains: tuple[str, ...] = ()
    expect_path_shape: str | None = None
    expect_routing_owner: str | None = None
    expect_routing_decision: str | None = None
    expect_task_types: tuple[str, ...] | None = None
    expect_planner_route_calls_delta: int | None = None
    expect_state: dict[str, object] = field(default_factory=dict)
    expectation: ReadinessExpectation | None = None
    mutation_id: str | None = None


@dataclass(frozen=True)
class ConversationScenario:
    id: str
    initial_state: OrchestratorState
    turns: tuple[ConversationTurn, ...]
    planner: Any | None = None
    redis_client: Any | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)
    category: str = "uncategorized"
    tags: tuple[str, ...] = ()
    criticality: ReadinessCriticality = "correctness"


@dataclass(frozen=True)
class ConversationTurnResult:
    turn: ConversationTurn
    updates: dict[str, Any]
    state: OrchestratorState
    response_text: str


@dataclass(frozen=True)
class ConversationScenarioResult:
    final_state: OrchestratorState
    turns: tuple[ConversationTurnResult, ...]


_PER_TURN_RESET: dict[str, Any] = {
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


def _config_for_scenario(scenario: ConversationScenario) -> RunnableConfig:
    configurable: dict[str, Any] = {}
    if scenario.planner is not None:
        configurable["task_planner"] = scenario.planner
    if scenario.redis_client is not None:
        configurable["redis_client"] = scenario.redis_client
    configurable.update(scenario.config_overrides)
    return {"configurable": configurable, "recursion_limit": 50}


def _route_calls(planner: Any | None) -> int:
    value = getattr(planner, "route_calls", 0)
    return int(value) if isinstance(value, int) else 0


def _assert_turn_expectations(
    *,
    scenario_id: str,
    turn_index: int,
    turn: ConversationTurn,
    result: ConversationTurnResult,
    route_calls_before: int,
    route_calls_after: int,
) -> None:
    label = f"{scenario_id} turn {turn_index}: {turn.user!r}"
    response_text = result.response_text

    for expected in turn.expect_response_contains:
        assert expected in response_text, f"{label} expected response to contain {expected!r}; got {response_text!r}"

    for forbidden in turn.expect_response_not_contains:
        assert forbidden not in response_text, f"{label} expected response not to contain {forbidden!r}"

    if turn.expect_path_shape is not None:
        assert result.state.semantic_path_shape == turn.expect_path_shape, label

    if turn.expect_routing_owner is not None:
        assert result.state.routing_owner == turn.expect_routing_owner, label

    if turn.expect_routing_decision is not None:
        assert result.state.routing_decision == turn.expect_routing_decision, label

    if turn.expect_task_types is not None:
        task_types = tuple(task.type for task in result.state.tasks.values())
        assert task_types == turn.expect_task_types, (
            f"{label} expected task types {turn.expect_task_types}; got {task_types}"
        )

    if turn.expect_planner_route_calls_delta is not None:
        delta = route_calls_after - route_calls_before
        assert delta == turn.expect_planner_route_calls_delta, (
            f"{label} expected route-call delta {turn.expect_planner_route_calls_delta}; got {delta}"
        )

    for field_name, expected_value in turn.expect_state.items():
        actual_value = getattr(result.state, field_name)
        assert actual_value == expected_value, (
            f"{label} expected state.{field_name}={expected_value!r}; got {actual_value!r}"
        )

    expectation = turn.expectation
    if expectation is None:
        return
    lowered = response_text.casefold()
    if expectation.expect_any:
        assert any(value.casefold() in lowered for value in expectation.expect_any), label
    for value in expectation.expect_all:
        assert value.casefold() in lowered, label
    for value in expectation.expect_none:
        assert value.casefold() not in lowered, label
    task_types = tuple(task.type for task in result.state.tasks.values())
    if expectation.expect_task_types is not None:
        assert task_types == expectation.expect_task_types, label
    if expectation.expect_allowed_task_types is not None:
        assert all(task_type in expectation.expect_allowed_task_types for task_type in task_types), label
    assert not set(task_types).intersection(expectation.expect_forbidden_task_types), label
    if expectation.expect_active_domain is not None:
        assert result.state.active_domain == expectation.expect_active_domain, label
    if expectation.expect_clarification_type is not None:
        pending = result.state.pending_query_clarification or {}
        raw = pending.get("pending_clarification") if isinstance(pending, dict) else None
        clarification = raw if isinstance(raw, dict) else pending
        assert clarification.get("clarification_type") == expectation.expect_clarification_type, label
    for field_name, expected_value in expectation.expect_state_fields:
        assert getattr(result.state, field_name) == expected_value, label


async def run_conversation_scenario(scenario: ConversationScenario) -> ConversationScenarioResult:
    state = scenario.initial_state
    config = _config_for_scenario(scenario)
    results: list[ConversationTurnResult] = []

    for index, turn in enumerate(scenario.turns, start=1):
        state = state.model_copy(update={**_PER_TURN_RESET, "last_message_text": turn.user})
        route_calls_before = _route_calls(scenario.planner)
        updates = await session_gate_direct_path(state, config)
        state = state.model_copy(update=updates)
        route_calls_after = _route_calls(scenario.planner)
        result = ConversationTurnResult(
            turn=turn,
            updates=updates,
            state=state,
            response_text=str(state.final_response or ""),
        )
        _assert_turn_expectations(
            scenario_id=scenario.id,
            turn_index=index,
            turn=turn,
            result=result,
            route_calls_before=route_calls_before,
            route_calls_after=route_calls_after,
        )
        results.append(result)

    return ConversationScenarioResult(final_state=state, turns=tuple(results))


async def run_readiness_scenario_fast(
    scenario: ReadinessScenario,
    *,
    initial_state: OrchestratorState,
    planner: Any | None = None,
    redis_client: Any | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> ConversationScenarioResult:
    """Run a canonical readiness scenario through the fast deterministic gate harness."""
    return await run_conversation_scenario(
        ConversationScenario(
            id=scenario.id,
            initial_state=initial_state,
            turns=tuple(
                ConversationTurn(user=turn.text, expectation=turn.expectation, mutation_id=turn.mutation_id)
                for turn in scenario.turns
            ),
            planner=planner,
            redis_client=redis_client,
            config_overrides=config_overrides or {},
            category=scenario.category,
            tags=scenario.tags,
            criticality=scenario.criticality,
        )
    )


__all__ = [
    "ConversationScenario",
    "ConversationScenarioResult",
    "ConversationTurn",
    "ConversationTurnResult",
    "run_conversation_scenario",
    "run_readiness_scenario_fast",
]
