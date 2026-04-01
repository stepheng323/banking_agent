"""Direct-path gate tests for conversational i18n behavior."""

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    ActiveSession,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.gate import session_gate_direct_path
from shared.i18n import render_cancelled_prompt, render_locale_switched, render_message
from shared.types.planner import SemanticRouteDecision


def _apply_updates(state: OrchestratorState, updates: dict[str, object]) -> OrchestratorState:
    return state.model_copy(update=updates)


class _MockTransferNeedsInputWorker:
    def __init__(self) -> None:
        self.call_count = 0
        self.last_payload: dict | None = None

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        self.call_count += 1
        self.last_payload = dict(payload)
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt="I need account details for this recipient.",
        )


async def test_gate_handles_greeting_meta_deterministically() -> None:
    state = OrchestratorState(
        user_id="u_gate_1",
        phone_number="2348777777777",
        channel="whatsapp",
        last_message_text="hi",
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.greeting", "en")
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "meta_direct"


async def test_gate_handles_capitalized_greeting_meta_before_query_routing() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for plain greeting",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_1b",
        phone_number="2348777777778",
        channel="whatsapp",
        last_message_text="Hi",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.greeting", "en")
    assert "tasks" not in updates or "direct_query" not in updates["tasks"]


async def test_gate_handles_capability_question_meta_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for deterministic capability question",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_cap_1",
        phone_number="2348777777780",
        channel="whatsapp",
        last_message_text="what can you do",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.capability_question", "en")


async def test_gate_handles_identity_question_meta_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for deterministic identity question",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ident_1",
        phone_number="2348777777781",
        channel="whatsapp",
        last_message_text="who are you",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.identity", "en")


async def test_gate_handles_pidgin_capability_question_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for deterministic pidgin capability question",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_cap_pcm_1",
        phone_number="2348777777782",
        channel="whatsapp",
        last_message_text="wetin you fit do",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["loaded_context"]["language"] == "pcm"
    assert updates["final_response"] == render_message("conversational.capability_question", "pcm")


async def test_gate_handles_yoruba_greeting_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for deterministic yoruba greeting",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_greet_yo_1",
        phone_number="2348777777783",
        channel="whatsapp",
        last_message_text="pele o",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "yo"
    assert updates["final_response"] == render_message("conversational.greeting", "yo")


async def test_gate_handles_hausa_identity_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for deterministic hausa identity question",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ident_ha_1",
        phone_number="2348777777784",
        channel="whatsapp",
        last_message_text="kai wa ne",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "ha"
    assert updates["final_response"] == render_message("conversational.identity", "ha")


async def test_gate_handles_igbo_appreciation_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for deterministic igbo appreciation",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_app_ig_1",
        phone_number="2348777777785",
        channel="whatsapp",
        last_message_text="dalu",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "ig"
    assert updates["final_response"] == render_message("conversational.appreciation", "ig")


async def test_gate_explicit_cancel_during_pending_interrupt_resets_immediately() -> None:
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

    updates = await session_gate_direct_path(state, config)
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_cancelled_prompt("en")
    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0


async def test_gate_query_shortcut_followup_bypasses_semantic_router_without_pending_interrupt() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active query continuation",
        )
    )
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
        stashed_query_session={"session_active": True, "query_result": {"summary_text": "Showing 1-5 of 8"}},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)
    assert updates.get("direct_path_triggered") is True
    assert planner.route_calls == 0
    assert updates["semantic_path_shape"] == "query_followup_bypass"
    assert updates["routing_owner"] == "query_session"
    assert updates["routing_decision"] == "query_followup_bypass"
    assert updates["routing_target_domain"] == "query"
    assert updates["routing_mode"] == "continuation"
    assert updates.get("waves") == [["direct_query"]]


async def test_gate_bypasses_planner_for_pure_query_detail_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_1",
        phone_number="2348999999901",
        channel="whatsapp",
        last_message_text="Show my last transaction",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "Show my last transaction"
    assert task.payload["force_new_query"] is True


async def test_gate_bypasses_planner_for_pure_query_analytics_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_2",
        phone_number="2348999999902",
        channel="whatsapp",
        last_message_text="How much did I spend yesterday",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_query_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["force_new_query"] is True


async def test_gate_bypasses_planner_for_pure_query_sent_analytics_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_2b",
        phone_number="2348999999912",
        channel="whatsapp",
        last_message_text="How much have I sent to Mum this week",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_query_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["force_new_query"] is True


async def test_gate_bypasses_planner_for_pure_query_have_i_sent_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_2c",
        phone_number="2348999999913",
        channel="whatsapp",
        last_message_text="Have I sent money today",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["force_new_query"] is True


async def test_gate_bypasses_planner_for_pure_query_beneficiary_ranking_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_3",
        phone_number="2348999999903",
        channel="whatsapp",
        last_message_text="Who did I send money to the most this week",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_query_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["force_new_query"] is True


async def test_gate_direct_query_bypass_forces_new_query_with_active_query_session() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_4",
        phone_number="2348999999904",
        channel="whatsapp",
        last_message_text="Show my last transaction",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.payload["force_new_query"] is True


async def test_gate_mixed_query_and_transfer_turn_still_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.96,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="mixed turn",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_5",
        phone_number="2348999999905",
        channel="whatsapp",
        last_message_text="Send 5k to Mum and show my last transaction",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_mixed"


async def test_gate_transfer_fastpath_does_not_steal_active_transfer_correction() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.92,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active flow correction",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_transfer_correction_1",
        phone_number="2348999999905",
        channel="whatsapp",
        last_message_text="make it 20k",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 10000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="BLOCK")],
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates


async def test_gate_transfer_fastpath_does_not_run_for_quoted_replay_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            mode="quoted_replay",
            target_intent="transfer",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="quoted replay",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_transfer_quote_1",
        phone_number="2348999999906",
        channel="whatsapp",
        last_message_text="send it again",
        has_quote=True,
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert updates["routing_owner"] == "planner"


async def test_gate_mixed_query_and_airtime_turn_still_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.96,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["airtime"],
            reason="mixed turn",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_clarification_bypass_6",
        phone_number="2348999999906",
        channel="whatsapp",
        last_message_text="Buy airtime and how much did I spend today",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["airtime"]


async def test_gate_semantic_router_routes_income_query_clarification_bypass_to_query_worker() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.97,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh inflow analytics query",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_query_1",
        phone_number="2348999999916",
        channel="whatsapp",
        last_message_text="What's my income this month",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_query_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "What's my income this month"
    assert task.payload["force_new_query"] is True


async def test_gate_semantic_router_routes_account_list_direct_to_account_worker() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_account",
            mode="new",
            target_intent="account",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="single-domain account request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_account_1",
        phone_number="2348999999917",
        channel="whatsapp",
        last_message_text="Show my linked accounts",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_account"]
    assert task.type == "account"
    assert task.payload["message"] == "Show my linked accounts"


async def test_gate_deterministic_transfer_fastpath_bypasses_router_and_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            mode="new",
            target_intent="transfer",
            confidence=0.94,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="single-domain transfer request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_1",
        phone_number="2348999999918",
        channel="whatsapp",
        last_message_text="Send 5k to Mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_transfer_domain"
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "fresh_transfer_command"
    task = updates["tasks"]["direct_transfer"]
    assert task.type == "transfer"
    assert task.payload["message"] == "Send 5k to Mum"
    assert "skip_extraction" not in task.payload


async def test_gate_batch_transfer_turn_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="multi transfer batch requires decomposition",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_batch_1",
        phone_number="2348999999918",
        channel="whatsapp",
        last_message_text="okay send 10k each to mum, tolu and doyin",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_mixed"


async def test_gate_split_transfer_turn_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="split transfer batch requires decomposition",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_batch_2",
        phone_number="2348999999919",
        channel="whatsapp",
        last_message_text="split 20k 70/30 btw mum and gaines",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_mixed"


async def test_gate_account_aware_transfer_turn_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="account-aware transfer requires planning",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_batch_3",
        phone_number="2348999999920",
        channel="whatsapp",
        last_message_text="send half my zenith to mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_mixed"


async def test_gate_deterministic_transfer_fastpath_still_executes_through_transfer_worker() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            mode="new",
            target_intent="transfer",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="single-domain transfer request",
        )
    )
    worker = _MockTransferNeedsInputWorker()
    state = OrchestratorState(
        user_id="u_gate_router_transfer_exec_1",
        phone_number="2348999999921",
        channel="whatsapp",
        last_message_text="Send 5k to Mum",
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "bank_name": "Zenith Bank",
                    "account_number": "00009384",
                    "mandate_status": "ready",
                },
                {
                    "bank_name": "First Bank",
                    "account_number": "0334557890",
                    "mandate_status": "pending",
                },
            ],
        },
    )
    gate_config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    gate_updates = await session_gate_direct_path(state, gate_config)
    routed_state = _apply_updates(state, gate_updates)
    execution_config: RunnableConfig = {
        "configurable": {"task_planner": planner, "services": {"transfer": worker}, "redis_client": None},
        "recursion_limit": 50,
    }

    execution_updates = await advance_wave(routed_state, execution_config)

    assert planner.route_calls == 0
    assert worker.call_count == 1
    assert worker.last_payload is not None
    assert worker.last_payload["message"] == "Send 5k to Mum"
    assert execution_updates["pending_interrupt"] is not None
    assert execution_updates["pending_interrupt"].kind == "input"
    assert execution_updates["outbox"]


async def test_gate_semantic_router_can_switch_language_before_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.98,
            detected_language="English",
            requested_language="Pidgin",
            response_key="conversational.capability_question",
            response=None,
            expected_transaction_executors=[],
            reason="language switch request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_4",
        phone_number="2348000000004",
        channel="whatsapp",
        last_message_text="Can you switch to Pidgin?",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "pcm"
    assert updates["loaded_context"]["detected_language"] == "pcm"
    assert updates["final_response"] == render_locale_switched("pcm")
    assert planner.plan_calls == 0


async def test_gate_semantic_router_locale_switch_can_run_during_pending_interrupt_without_reset() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.98,
            detected_language="English",
            requested_language="Yoruba",
            response_key="conversational.capability_question",
            response=None,
            expected_transaction_executors=[],
            reason="language switch request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_4b",
        phone_number="2348000000005",
        channel="whatsapp",
        last_message_text="speak Yoruba now",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"]),
        session_stack=[ActiveSession(domain="transfer", state="WAITING_FOR_INPUT", interrupt_policy="BLOCK")],
        tasks={"t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.DRAFT, payload={"foo": "bar"})},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "yo"
    assert updates["loaded_context"]["detected_language"] == "yo"
    assert updates["final_response"] == render_locale_switched("yo")
    assert "tasks" not in updates
    assert "pending_interrupt" not in updates
    assert "session_stack" not in updates
    assert planner.plan_calls == 0


async def test_gate_semantic_router_locale_switch_persists_language_in_redis(monkeypatch) -> None:
    redis_client = _TrackingLocaleRedis()
    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: redis_client))

    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.99,
            detected_language="English",
            requested_language="Hausa",
            response_key="conversational.capability_question",
            response=None,
            expected_transaction_executors=[],
            reason="language switch request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_5",
        phone_number="2348000000006",
        channel="whatsapp",
        last_message_text="Can we continue in Hausa?",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "ha"
    assert updates["final_response"] == render_locale_switched("ha")
    assert redis_client.set_calls
    assert redis_client.set_calls[0][0] == "user:2348000000006:language"
    assert redis_client.set_calls[0][1] == "ha"


class _RouteTurnPlanner:
    def __init__(self, decision: SemanticRouteDecision) -> None:
        self._decision = decision
        self.route_calls = 0
        self.plan_calls = 0
        self.last_context: str | None = None

    async def route_semantic_turn(self, phone_number: str, text: str, context: str = "None") -> SemanticRouteDecision:
        del phone_number, text
        self.route_calls += 1
        self.last_context = context
        return self._decision

    async def plan_tasks(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.plan_calls += 1
        raise AssertionError("planner should not run when gate returns a direct router answer")


class _TrackingRedis:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1


class _TrackingLocaleRedis(_TrackingRedis):
    def __init__(self) -> None:
        super().__init__()
        self.set_calls: list[tuple[str, str, int | None]] = []
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls.append((key, value, ex))
        self.store[key] = value


class _TrackingRedisWithSession(_TrackingRedis):
    def __init__(self, payload: str | None) -> None:
        super().__init__()
        self.payload = payload

    async def get(self, key: str) -> str | None:
        if "query:session:" in key:
            return self.payload
        return None


async def test_gate_semantic_router_can_bypass_planner_with_direct_response() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.95,
            detected_language="Pidgin",
            response_key="conversational.checkin",
            response=None,
            expected_transaction_executors=[],
            reason="short check-in",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_5",
        phone_number="2348000000005",
        channel="whatsapp",
        last_message_text="help me",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert isinstance(updates.get("final_response"), str)
    assert updates["loaded_context"]["language"] == "pcm"


async def test_gate_semantic_router_direct_clarify_for_question_falls_through_to_planner(
    monkeypatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.orchestrator.nodes.gate.logger.info", _capture)

    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.67,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="unclear short question",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_5b",
        phone_number="23480000000051",
        channel="whatsapp",
        last_message_text="How do your limits work?",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_direct"
    assert updates["final_response"] == render_message("conversational.clarify", "en")
    assert not any(event == "unexpected_turn_route_breadcrumb" for event, _ in events)


async def test_gate_semantic_router_can_answer_grounded_account_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.96,
            detected_language="English",
            response_key=None,
            response="Your First Bank account is linked, but it is not ready for payments yet.",
            expected_transaction_executors=[],
            reason="grounded account readiness answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_1",
        phone_number="2348000000201",
        channel="telegram",
        last_message_text="Can I use first bank now",
        loaded_context={
            "language": "en",
            "accounts": [
                {"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"},
                {"bank_name": "First Bank", "account_number": "0334557890", "mandate_status": "pending"},
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "ACCOUNTS:" in (planner.last_context or "")
    assert "First Bank" in (planner.last_context or "")
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "Your First Bank account is linked, but it is not ready for payments yet."


async def test_gate_no_longer_overrides_meta_router_reply_with_grounded_account_fastpath() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.82,
            detected_language="English",
            response_key="conversational.identity",
            response=None,
            expected_transaction_executors=[],
            reason="misclassified meta reply",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_1b",
        phone_number="2348000000210",
        channel="telegram",
        last_message_text="Is my first bank account ready?",
        loaded_context={
            "language": "en",
            "accounts": [
                {"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"},
                {
                    "bank_name": "First Bank",
                    "account_number": "0334557890",
                    "mandate_status": "pending",
                    "transfer_destinations": [{"channel": "ussd", "url": "bank://activate"}],
                },
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_message("conversational.identity", "en")


async def test_gate_semantic_router_can_answer_grounded_account_follow_up_with_typo() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.94,
            detected_language="English",
            response_key=None,
            response="Your First Bank account is linked, but it is not ready for payments yet.",
            expected_transaction_executors=[],
            reason="grounded account readiness answer with typo tolerance",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_2",
        phone_number="2348000000202",
        channel="telegram",
        last_message_text="Can I use fisr bank now",
        loaded_context={
            "language": "en",
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0334557890", "mandate_status": "pending"},
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "Your First Bank account is linked, but it is not ready for payments yet."


async def test_gate_semantic_router_can_answer_grounded_beneficiary_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response="Yes, you still have Mum saved on Opay ending in 1023.",
            expected_transaction_executors=[],
            reason="grounded beneficiary existence answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_3",
        phone_number="2348000000203",
        channel="whatsapp",
        last_message_text="Do I still have mum saved",
        loaded_context={
            "language": "en",
            "beneficiaries": [
                {
                    "alias": "Mum",
                    "account_name": "Mercy Johnson",
                    "bank_name": "Opay",
                    "account_number": "8162511023",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "BENEFICIARIES:" in (planner.last_context or "")
    assert "Mum" in (planner.last_context or "")
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "Yes, you still have Mum saved on Opay ending in 1023."


async def test_gate_semantic_router_can_answer_grounded_beneficiary_preview_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response="You have Tolu Adedayo on First Bank ending in 5261.",
            expected_transaction_executors=[],
            reason="grounded beneficiary preview answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_3b",
        phone_number="2348000000208",
        channel="whatsapp",
        last_message_text="Which Tolu do I have saved",
        loaded_context={
            "language": "en",
            "beneficiaries": [
                {
                    "alias": "Tolu",
                    "account_name": "Tolu Adedayo",
                    "bank_name": "First Bank",
                    "account_number": "0760505261",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "BENEFICIARIES:" in (planner.last_context or "")
    assert "Tolu" in (planner.last_context or "")
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "You have Tolu Adedayo on First Bank ending in 5261."


async def test_gate_semantic_router_can_answer_grounded_default_account_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response="Your default account is Zenith Bank ending in 9384.",
            expected_transaction_executors=[],
            reason="grounded default-account answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_3c",
        phone_number="2348000000209",
        channel="telegram",
        last_message_text="Which account is default now",
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "bank_name": "Zenith Bank",
                    "account_number": "00009384",
                    "mandate_status": "ready",
                    "is_default": True,
                },
                {"bank_name": "First Bank", "account_number": "0334557890", "mandate_status": "pending"},
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "ACCOUNTS:" in (planner.last_context or "")
    assert "default" in (planner.last_context or "").lower()
    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "Your default account is Zenith Bank ending in 9384."


async def test_gate_semantic_router_can_answer_grounded_query_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.94,
            detected_language="English",
            response_key=None,
            response="Yes. The transactions shown after that include more debits.",
            expected_transaction_executors=[],
            reason="grounded query recap answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_4",
        phone_number="2348000000204",
        channel="whatsapp",
        last_message_text="Any more debits after that",
        loaded_context={"language": "en"},
    )

    class _RedisWithQuerySession:
        async def get(self, key: str) -> str | None:
            if "query:session:" in key:
                return (
                    '{"session_active": true, "query_result": {"summary_text": "Recent results include 3 debits and 1 credit."}}'
                )
            return None

    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": _RedisWithQuerySession()},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_direct"
    assert updates["final_response"] == "Yes. The transactions shown after that include more debits."


async def test_gate_semantic_router_can_answer_grounded_flow_recap_without_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.96,
            detected_language="English",
            response_key=None,
            response="We are on your transfer. I still have your amount and recipient, and the flow is waiting to continue from there.",
            expected_transaction_executors=[],
            reason="grounded active-flow recap answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_5",
        phone_number="2348000000205",
        channel="whatsapp",
        last_message_text="Where did we stop",
        loaded_context={"language": "en"},
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 5000, "recipient_name": "Tolu"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "ACTIVE_FLOW:" in (planner.last_context or "")
    assert "Current Task Data:" in (planner.last_context or "")
    assert updates["direct_path_triggered"] is True
    assert (
        updates["final_response"]
        == "We are on your transfer. I still have your amount and recipient, and the flow is waiting to continue from there."
    )


async def test_gate_semantic_router_out_of_scope_includes_empathy_and_redirect() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.97,
            detected_language="English",
            response_key="conversational.out_of_scope",
            response="I hear you.",
            expected_transaction_executors=[],
            reason="non-banking emotional turn",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_oos_1",
        phone_number="2348000000111",
        channel="whatsapp",
        last_message_text="I am very hungry",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "I hear you.\n" + render_message("conversational.out_of_scope", "en")


async def test_gate_semantic_router_out_of_scope_without_empathy_uses_redirect_only() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.97,
            detected_language="English",
            response_key="conversational.out_of_scope",
            response=None,
            expected_transaction_executors=[],
            reason="non-banking out-of-scope",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_oos_2",
        phone_number="2348000000112",
        channel="whatsapp",
        last_message_text="book me a flight",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_message("conversational.out_of_scope", "en")


async def test_gate_semantic_router_passes_expected_executors_without_direct_path() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.91,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer", "airtime"],
            reason="explicit mixed transaction request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_6",
        phone_number="2348000000006",
        channel="whatsapp",
        last_message_text="send 10k and buy 5k airtime",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates.get("direct_path_triggered") is None
    assert updates["preplanner_expected_transaction_executors"] == ["transfer", "airtime"]


async def test_gate_direct_path_routes_balance_request_without_turn_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="unused because balance fastpath should win first",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_7",
        phone_number="2348000000007",
        channel="whatsapp",
        last_message_text="check my balance",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["waves"] == [["direct_account_balance"]]
    task = updates["tasks"]["direct_account_balance"]
    assert task.type == "account"
    assert task.payload["action"] == "check_balance"


async def test_gate_query_session_does_not_swallow_full_query_restatement_as_fast_resume() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh query restatement",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_1",
        phone_number="2348000000071",
        channel="whatsapp",
        last_message_text="Show my last transaction",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "Show my last transaction"
    assert task.payload["force_new_query"] is True


async def test_gate_routes_last_transaction_surface_to_structured_path() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.91,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="structured query surface",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_surface_1",
        phone_number="2348000000301",
        channel="whatsapp",
        last_message_text="Show my last transaction",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "Show my last transaction"
    assert task.payload["force_new_query"] is True


async def test_gate_blocks_router_direct_text_for_linked_accounts_surface() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_account",
            mode="new",
            target_intent="account",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="structured account surface",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_surface_2",
        phone_number="2348000000302",
        channel="telegram",
        last_message_text="What linked accounts do I have?",
        loaded_context={
            "language": "en",
            "accounts": [
                {"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"},
                {"bank_name": "First Bank", "account_number": "0334557890", "mandate_status": "pending"},
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_account"]
    assert task.type == "account"


async def test_gate_balance_fastpath_does_not_swallow_mixed_transaction_and_balance_request() -> None:
    state = OrchestratorState(
        user_id="u_gate_7b",
        phone_number="23480000000071",
        channel="whatsapp",
        last_message_text="Send 10k to gaines, buy 1k airtime to my line, and show my balance",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert "turn_context_summary" in updates
    assert updates.get("semantic_path_shape") is None


async def test_gate_query_session_ignores_generic_checkin_direct_response_for_follow_up_question() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.87,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active query continuation",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_2",
        phone_number="2348000000072",
        channel="whatsapp",
        last_message_text="Really?",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
        stashed_query_session={"session_active": True, "query_result": {"summary_text": "No spend yesterday."}},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "Really?"


async def test_gate_routes_show_me_active_query_followup_directly_to_query_worker() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.87,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active query continuation",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_followup_1",
        phone_number="2348000002072",
        channel="whatsapp",
        last_message_text="show me",
        loaded_context={"language": "en"},
        stashed_query_session={
            "session_active": True,
            "query_result": {"summary_text": "You spent ₦60,000 on mum this week."},
            "query_contract": {
                "intent": "analytics_summary",
                "time_start": "2026-03-16",
                "time_end": "2026-03-19",
                "timezone": "Africa/Lagos",
                "normalized_query": {
                    "intent": "analytics_summary",
                    "time_range": {"start": "2026-03-16", "end": "2026-03-19", "granularity": "week"},
                    "filters": {"transaction_type": "debit", "merchant": ["mum"]},
                    "accounts_scope": "all",
                },
            },
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "show me"
    assert task.payload.get("force_new_query") is None


async def test_gate_routes_last_week_active_query_followup_directly_to_query_worker() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.84,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active query continuation",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_followup_2",
        phone_number="2348000002073",
        channel="whatsapp",
        last_message_text="What about last week",
        loaded_context={"language": "en"},
        stashed_query_session={
            "session_active": True,
            "query_result": {"summary_text": "You spent ₦60,000 on mum this week."},
            "query_contract": {
                "intent": "analytics_summary",
                "time_start": "2026-03-16",
                "time_end": "2026-03-19",
                "timezone": "Africa/Lagos",
                "normalized_query": {
                    "intent": "analytics_summary",
                    "time_range": {"start": "2026-03-16", "end": "2026-03-19", "granularity": "week"},
                    "filters": {"transaction_type": "debit", "merchant": ["mum"]},
                    "accounts_scope": "all",
                },
            },
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "What about last week"


async def test_gate_routes_how_much_total_active_query_followup_directly_to_query_worker() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.74,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active query continuation",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_followup_2b",
        phone_number="23480000020735",
        channel="whatsapp",
        last_message_text="How much total",
        loaded_context={"language": "en"},
        stashed_query_session={
            "session_active": True,
            "query_result": {"summary_text": "You showed 5 transactions to Mum this month.", "items": []},
            "query_contract": {
                "intent": "transaction_list",
                "time_start": "2026-03-01",
                "time_end": "2026-03-19",
                "timezone": "Africa/Lagos",
                "normalized_query": {
                    "intent": "transaction_list",
                    "time_range": {"start": "2026-03-01", "end": "2026-03-19", "granularity": "month"},
                    "filters": {"transaction_type": "debit", "merchant": ["mum"]},
                    "accounts_scope": "all",
                },
            },
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "How much total"


async def test_gate_logs_query_routing_breadcrumb_for_active_query_handoff(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.orchestrator.nodes.gate.logger.info", _capture)

    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            target_intent="query",
            confidence=0.9,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="active query continuation",
        )
    )

    state = OrchestratorState(
        user_id="u_gate_query_followup_3",
        phone_number="2348000002074",
        channel="whatsapp",
        last_message_text="show me",
        loaded_context={"language": "en"},
        stashed_query_session={
            "session_active": True,
            "query_result": {"summary_text": "You spent ₦60,000 on mum this week."},
            "query_contract": {
                "intent": "analytics_summary",
                "time_start": "2026-03-16",
                "time_end": "2026-03-19",
                "timezone": "Africa/Lagos",
                "normalized_query": {
                    "intent": "analytics_summary",
                    "time_range": {"start": "2026-03-16", "end": "2026-03-19", "granularity": "week"},
                    "filters": {"transaction_type": "debit", "merchant": ["mum"]},
                    "accounts_scope": "all",
                },
            },
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["semantic_path_shape"] == "semantic_router_domain"
    assert (
        "gate_semantic_router_domain_dispatch",
        {
            "decision": "domain_query",
            "domain": "query",
            "mode": "continuation",
            "task_id": "direct_query",
        },
    ) in events


async def test_gate_exits_active_query_session_on_greeting_direct_reply() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.89,
            detected_language="English",
            response_key="conversational.greeting",
            response=None,
            expected_transaction_executors=[],
            reason="greeting during active query session",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_conv_1",
        phone_number="2348000001072",
        channel="whatsapp",
        last_message_text="Hi",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
        stashed_query_session={
            "session_active": True,
            "query_contract": {"intent": "transaction_search"},
            "query_result": {"summary_text": "Result"},
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.greeting", "en")
    assert updates["stashed_query_session"] is None
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert "tasks" not in updates or "direct_query" not in updates["tasks"]


async def test_gate_semantic_router_cancel_response_clears_query_state() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.98,
            detected_language="English",
            response_key="planner.cancelled",
            response=None,
            expected_transaction_executors=[],
            reason="explicit cancel",
        )
    )
    redis_client = _TrackingRedis()
    state = OrchestratorState(
        user_id="u_gate_8",
        phone_number="2348000000008",
        channel="whatsapp",
        last_message_text="please cancel",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
        tasks={"t1": TaskSpec(id="t1", type="query", stage=TaskStage.DRAFT, payload={"message": "more"})},
        waves=[["t1"]],
        current_wave_index=0,
        stashed_query_session={"session_active": True},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_direct"
    assert updates["final_response"] == render_cancelled_prompt("en")
    assert updates["stashed_query_session"] is None
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert redis_client.deleted_keys


async def test_gate_explicit_cancel_without_active_state_returns_clarify() -> None:
    state = OrchestratorState(
        user_id="u_gate_8b",
        phone_number="2348000000018",
        channel="whatsapp",
        last_message_text="abort",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"redis_client": None}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_message("conversational.clarify", "en")


async def test_gate_explicit_cancel_during_pending_query_clarification_uses_query_goodbye() -> None:
    redis_client = _TrackingRedisWithSession(
        '{"session_active": true, "pending_clarification": {"kind": "pending_clarification", "original_query": "How much did I spend last", "current_intent": "spending_total", "original_extraction": {"intent": "spending_total", "filters": {}, "time_range": {"reference_type": "vague", "days_back": 30}, "requested_capabilities": [], "ambiguities": [{"code": "TIME_VAGUE", "context": "last"}], "raw_query": "How much did I spend last"}, "ambiguities": [{"code": "TIME_VAGUE", "context": "last"}], "resolver_message": "What time period did you mean by last?", "language": "en"}}'
    )
    state = OrchestratorState(
        user_id="u_gate_query_cancel_1",
        phone_number="2348000000019",
        channel="whatsapp",
        last_message_text="abort",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"redis_client": redis_client}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_message("query.session.goodbye", "en")
    assert redis_client.deleted_keys == ["query:session:2348000000019"]


async def test_gate_pending_query_clarification_time_reply_bypasses_semantic_router() -> None:
    redis_client = _TrackingRedisWithSession(
        '{"session_active": true, "pending_clarification": {"kind": "pending_clarification", "original_query": "How much did I spend last", "current_intent": "spending_total", "original_extraction": {"intent": "spending_total", "filters": {}, "time_range": {"reference_type": "vague", "days_back": 30}, "requested_capabilities": [], "ambiguities": [{"code": "TIME_VAGUE", "context": "last"}], "raw_query": "How much did I spend last"}, "ambiguities": [{"code": "TIME_VAGUE", "context": "last"}], "resolver_message": "What time period did you mean by last?", "language": "en"}}'
    )
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="continuation",
            confidence=0.9,
            detected_language="English",
            target_intent="query",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="pending query clarification answer",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_pending_1",
        phone_number="2348000000020",
        channel="whatsapp",
        last_message_text="last 3 days",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"redis_client": redis_client, "task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "query_followup_bypass"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "last 3 days"
    assert "force_new_query" not in task.payload


async def test_gate_stale_query_interrupt_is_cleared_before_fresh_query_routing() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.94,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="fresh income analytics query",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_query_interrupt_cleanup_1",
        phone_number="2348000000021",
        channel="whatsapp",
        last_message_text="What's my income this month",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["time_period"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="query",
                stage=TaskStage.EXTRACTED,
                payload={"message": "Show me my credit transactions"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_query_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "What's my income this month"
    assert task.payload["force_new_query"] is True


async def test_gate_direct_path_cancel_and_balance_cleans_query_and_runs_balance() -> None:
    redis_client = _TrackingRedis()
    state = OrchestratorState(
        user_id="u_gate_9",
        phone_number="2348000000009",
        channel="whatsapp",
        last_message_text="cancel and check my balance",
        loaded_context={"language": "en"},
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        active_domain="query",
        tasks={"t1": TaskSpec(id="t1", type="query", stage=TaskStage.DRAFT, payload={"message": "more"})},
        waves=[["t1"]],
        current_wave_index=0,
        stashed_query_session={"session_active": True},
    )
    config: RunnableConfig = {"configurable": {"redis_client": redis_client}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["waves"] == [["direct_account_balance"]]
    task = updates["tasks"]["direct_account_balance"]
    assert task.type == "account"
    assert task.payload["action"] == "check_balance"
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert updates["stashed_query_session"] is None
    assert redis_client.deleted_keys == ["query:session:2348000000009"]
