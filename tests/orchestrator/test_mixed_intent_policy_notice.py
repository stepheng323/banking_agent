"""Integration tests for mixed-intent policy notice behavior in orchestrator nodes."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.ingest import ingest_message
from apps.core.src.agent.orchestrator.nodes.planner import SAFE_CAPABILITY_FALLBACK, plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    """Mock planner used by orchestrator node integration tests."""

    planner_llm = None

    def __init__(self, output: PlannerOutput):
        self._output = output

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _MockTransferWorker:
    """Mock transfer worker that always requests more input."""

    async def run(self, payload, context, user_message=None, pin_verified=False):  # noqa: ANN001
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account"],
            prompt="Provide recipient account details.",
        )


def _apply(state: OrchestratorState, updates: dict) -> OrchestratorState:
    """Apply node updates to state."""
    return state.model_copy(update=updates)


@pytest.mark.asyncio
async def test_mixed_intent_outbox_contains_notice_then_transfer_prompt():
    """Mixed request should prepend unsupported notice then continue transfer flow."""
    planner_output = PlannerOutput(
        primary_intent="transfer",
        response="",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="send 10k to tolu and invest 10k",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to tolu",
                parameters=TaskParameters(amount="10000", recipient="tolu"),
                risk="MONEY_MOVE",
            )
        ],
    )

    state = OrchestratorState(
        user_id="u_1",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="send 10k to tolu and invest 10k",
    )

    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {"transfer": _MockTransferWorker()},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))
    state = _apply(state, await advance_wave(state, config))

    outbox = state.outbox
    assert len(outbox) >= 2
    assert "Investments" in outbox[0]["text"]
    assert "I can proceed with money transfer" in outbox[0]["text"]
    assert "I can help with send money or review recent transactions instead." in outbox[0]["text"]
    assert "I need account details for tolu." in outbox[1]["text"]
    assert state.policy_notice is None


@pytest.mark.asyncio
async def test_open_world_fallback_when_no_supported_tasks():
    """No supported tasks and no known unsupported class should use safe fallback."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        confidence=0.2,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="i want to transfer to pension fund",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_2",
        phone_number="2348111111111",
        channel="whatsapp",
        last_message_text="i want to transfer to pension fund",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == SAFE_CAPABILITY_FALLBACK
