from typing import NoReturn

import pytest

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.execution.wave import engine as execution_wave_engine
from apps.chat.src.agent.orchestrator.workflows.execution.wave.engine import run_execution_wave


def _config() -> dict[str, object]:
    return {"configurable": {}, "recursion_limit": 50}


def _state_with_task(*, task_stage: TaskStage, pending_interrupt: PendingInterrupt | None = None) -> OrchestratorState:
    task = TaskSpec(
        id="task_transfer",
        type="transfer",
        stage=task_stage,
        payload={"amount": "1000"},
    )
    return OrchestratorState(
        user_id="u_execution_wave_contract",
        phone_number="2348011115500",
        channel="whatsapp",
        last_message_text="send money",
        tasks={"task_transfer": task},
        waves=[["task_transfer"]],
        current_wave_index=0,
        pending_interrupt=pending_interrupt,
        loaded_context={"language": "en"},
    )


async def test_execution_wave_pending_interrupt_stops_before_runtime_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=["task_transfer"],
        fields_by_task={"task_transfer": ["amount"]},
        prompt="Need amount.",
    )
    state = _state_with_task(task_stage=TaskStage.DRAFT, pending_interrupt=interrupt)

    def fail_runtime_setup(*_: object, **__: object) -> NoReturn:
        raise AssertionError("runtime setup should not run with a pending interrupt")

    monkeypatch.setattr(execution_wave_engine, "build_execution_wave_runtime", fail_runtime_setup)

    result = await run_execution_wave(state, _config())

    assert result.phase == "pending_interrupt"
    assert result.worker_phase_entered is False
    assert result.pending_interrupt_kind == "input"
    assert result.updates == {"pending_interrupt": interrupt}


async def test_advance_wave_completed_wave_still_advances_current_wave_index() -> None:
    state = _state_with_task(task_stage=TaskStage.COMPLETED)

    updates = await advance_wave(state, _config())

    assert updates["current_wave_index"] == 1
