"""Fast-path gate tests for conversational i18n behavior."""

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.gate import session_gate_fastpath
from shared.types.planner import InterruptRouteDecision


class _RouterPlanner:
    def __init__(self, decision: InterruptRouteDecision) -> None:
        self._decision = decision

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        return self._decision


class _FailingRouterPlanner:
    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> InterruptRouteDecision:
        del phone_number, text, context
        raise RuntimeError("router unavailable")


async def test_gate_defers_greeting_meta_to_planner() -> None:
    state = OrchestratorState(
        user_id="u_gate_1",
        phone_number="2348777777777",
        channel="whatsapp",
        last_message_text="hi",
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)
    assert updates == {}


async def test_gate_defers_brand_origin_meta_to_planner() -> None:
    state = OrchestratorState(
        user_id="u_gate_1b",
        phone_number="2348777777778",
        channel="whatsapp",
        last_message_text="who made you",
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)
    assert updates == {}


async def test_gate_fast_path_cancel_uses_localized_keyed_message() -> None:
    state = OrchestratorState(
        user_id="u_gate_2",
        phone_number="2348888888888",
        channel="whatsapp",
        last_message_text="cancel",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"]),
        session_stack=[
            ActiveSession(
                domain="transfer",
                state="WAITING_FOR_INPUT",
                interrupt_policy="BLOCK",
            )
        ],
        loaded_context={"language": "pcm"},
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)
    assert updates.get("final_response") == "Don cancel."
    assert updates.get("fast_path_triggered") is True


async def test_gate_pending_input_defers_intent_switch_to_planner() -> None:
    state = OrchestratorState(
        user_id="u_gate_3",
        phone_number="2348999999999",
        channel="whatsapp",
        last_message_text="Show my beneficiaries",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"]),
        session_stack=[
            ActiveSession(
                domain="transfer",
                state="WAITING_FOR_INPUT",
                interrupt_policy="BLOCK",
            )
        ],
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.EXTRACTED, payload={"recipient_name": "Tolu"})
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _RouterPlanner(
                InterruptRouteDecision(
                    decision="switch_intent",
                    confidence=0.91,
                    detected_language="Yoruba",
                    target_intent="beneficiary",
                    reason="User asked to list beneficiaries.",
                )
            )
        },
        "recursion_limit": 50,
    }

    updates = await session_gate_fastpath(state, config)
    assert updates == {}


async def test_gate_pending_input_keeps_fast_path_for_slot_filling() -> None:
    state = OrchestratorState(
        user_id="u_gate_4",
        phone_number="2348000000001",
        channel="whatsapp",
        last_message_text="0123456789",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"]),
        session_stack=[
            ActiveSession(
                domain="transfer",
                state="WAITING_FOR_INPUT",
                interrupt_policy="BLOCK",
            )
        ],
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.EXTRACTED, payload={"recipient_name": "Tolu"})
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _RouterPlanner(
                InterruptRouteDecision(
                    decision="continue_flow",
                    confidence=0.94,
                    detected_language="English",
                    target_intent=None,
                    reason="Looks like recipient detail slot filling.",
                )
            )
        },
        "recursion_limit": 50,
    }

    updates = await session_gate_fastpath(state, config)
    assert updates.get("fast_path_triggered") is True
    assert updates.get("pending_interrupt") is None
    assert updates.get("last_interrupt") is not None


async def test_gate_pending_input_router_failure_defers_to_planner() -> None:
    state = OrchestratorState(
        user_id="u_gate_5",
        phone_number="2348000000002",
        channel="whatsapp",
        last_message_text="show beneficiaries",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"]),
        session_stack=[
            ActiveSession(
                domain="transfer",
                state="WAITING_FOR_INPUT",
                interrupt_policy="BLOCK",
            )
        ],
        tasks={"t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.EXTRACTED, payload={})},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _FailingRouterPlanner()},
        "recursion_limit": 50,
    }

    updates = await session_gate_fastpath(state, config)
    assert updates == {}
