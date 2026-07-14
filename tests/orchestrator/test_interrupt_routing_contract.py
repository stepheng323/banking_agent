import pytest

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RoutingContractError,
    TurnNextStep,
    TurnOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.outcome import commit_interrupt_outcome


def _state(*, current_wave_index: int = 0) -> OrchestratorState:
    task = TaskSpec(
        id="task_transfer",
        type="transfer",
        stage=TaskStage.DRAFT,
        payload={"amount": "1000"},
    )
    return OrchestratorState(
        user_id="u_interrupt_routing_contract",
        phone_number="2348011115600",
        channel="whatsapp",
        last_message_text="continue",
        tasks={task.id: task},
        waves=[[task.id]],
        current_wave_index=current_wave_index,
        loaded_context={"language": "en"},
    )


def test_interrupt_waiting_response_ends_turn() -> None:
    state = _state()
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=["task_transfer"],
        fields_by_task={"task_transfer": ["amount"]},
        prompt="Need amount.",
    )

    updates = commit_interrupt_outcome(
        state,
        {
            "pending_interrupt": interrupt,
            "outbox": [{"type": "say", "text": "Need amount."}],
            "path_shape": "interrupt_router_only",
        },
    )

    directive = updates["turn_directive"]
    assert directive.owner == "interrupt"
    assert directive.decision == "interrupt_router_only"
    assert directive.path_shape == "interrupt_router_only"
    assert directive.outcome_kind == TurnOutcomeKind.DIRECT_RESPONSE
    assert directive.next_step == TurnNextStep.END
    assert "path_shape" not in updates


def test_interrupt_approval_advances_existing_wave() -> None:
    updates = commit_interrupt_outcome(
        _state(),
        {"pending_interrupt": None, "path_shape": "interrupt_router_only"},
    )

    directive = updates["turn_directive"]
    assert directive.owner == "interrupt"
    assert directive.outcome_kind == TurnOutcomeKind.TASK_DISPATCH
    assert directive.next_step == TurnNextStep.ADVANCE


def test_interrupt_replacement_without_runnable_wave_returns_to_planner() -> None:
    updates = commit_interrupt_outcome(
        _state(current_wave_index=1),
        {"pending_interrupt": None, "path_shape": "interrupt_replan_switch"},
    )

    directive = updates["turn_directive"]
    assert directive.owner == "interrupt"
    assert directive.decision == "interrupt_replan_switch"
    assert directive.outcome_kind == TurnOutcomeKind.PLANNER_HANDOFF
    assert directive.next_step == TurnNextStep.PLAN


def test_interrupt_boundary_rejects_legacy_route_override() -> None:
    with pytest.raises(RoutingContractError, match="controlled routing fields"):
        commit_interrupt_outcome(
            _state(),
            {"pending_interrupt": None, "direct_path_triggered": True},
        )
