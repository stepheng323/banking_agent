"""Fast-path gate tests for conversational i18n behavior."""

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.gate import session_gate_fastpath


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


async def test_gate_pending_interrupt_always_defers_to_interrupt_node() -> None:
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
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)
    assert updates == {}


async def test_gate_query_fast_path_still_applies_without_pending_interrupt() -> None:
    state = OrchestratorState(
        user_id="u_gate_3",
        phone_number="2348999999999",
        channel="whatsapp",
        last_message_text="more",
        session_stack=[
            ActiveSession(
                domain="query",
                state="RUNNING",
                interrupt_policy="ALLOW",
            )
        ],
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)
    assert updates.get("fast_path_triggered") is True
    assert updates.get("waves") == [["fast_query_resume"]]
