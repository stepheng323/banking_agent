"""Direct-path gate tests for conversational i18n behavior."""

import json
import time

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import (
    ActiveSession,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.execution import advance_wave
from apps.chat.src.agent.orchestrator.nodes.gate.runner import session_gate_direct_path
from shared.i18n import render_cancelled_prompt, render_locale_switched, render_message
from shared.types.planner import ContextFrameFollowupDecision, SemanticRouteDecision


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


async def test_gate_handles_pidgin_social_greeting_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.4,
            detected_language="English",
            target_intent=None,
            expected_transaction_executors=[],
            reason="should not run for social greeting",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_pidgin_social_greeting",
        phone_number="2348777777777",
        channel="telegram",
        last_message_text="How far my guy",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.greeting", "pcm")
    assert updates["routing_owner"] == "guardrail"


async def test_gate_handles_prefixed_pidgin_social_greeting_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            confidence=0.7,
            detected_language="English",
            target_intent="transfer",
            expected_transaction_executors=["transfer"],
            reason="should not run for prefixed social greeting",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_prefixed_pidgin_social_greeting",
        phone_number="2348777777777",
        channel="telegram",
        last_message_text="My g, how far?",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.greeting", "pcm")
    assert updates["routing_owner"] == "guardrail"


async def test_gate_handles_presence_checkin_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            confidence=0.7,
            detected_language="English",
            target_intent="transfer",
            expected_transaction_executors=["transfer"],
            reason="should not run for presence checkin",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_presence_checkin",
        phone_number="2348777777777",
        channel="telegram",
        last_message_text="Are you there?",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.checkin", "en")
    assert updates["routing_owner"] == "guardrail"


async def test_gate_handles_pidgin_presence_checkin_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            confidence=0.7,
            detected_language="English",
            target_intent="transfer",
            expected_transaction_executors=["transfer"],
            reason="should not run for pidgin presence checkin",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_pidgin_presence_checkin",
        phone_number="2348777777777",
        channel="telegram",
        last_message_text="You dey?",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.checkin", "pcm")
    assert updates["routing_owner"] == "guardrail"


async def test_gate_handles_prefixed_pidgin_presence_checkin_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            confidence=0.7,
            detected_language="English",
            target_intent="transfer",
            expected_transaction_executors=["transfer"],
            reason="should not run for prefixed pidgin presence checkin",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_prefixed_pidgin_presence_checkin",
        phone_number="2348777777777",
        channel="telegram",
        last_message_text="My guy, how you dey",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "meta_direct"
    assert updates["final_response"] == render_message("conversational.checkin", "pcm")
    assert updates["routing_owner"] == "guardrail"


async def test_gate_filters_gibberish_without_semantic_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.4,
            detected_language="English",
            target_intent=None,
            expected_transaction_executors=[],
            reason="should not run for gibberish",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_gibberish",
        phone_number="2348000001111",
        channel="whatsapp",
        last_message_text="🔥🔥🔥🔥🔥",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "gibberish_direct"
    assert updates["final_response"] == render_message("common.gibberish_prompt", "en")
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "gibberish_filtered"


async def test_gate_routes_recent_batch_receipt_followup_to_support_without_planner() -> None:
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
            reason="should not run for recent batch receipt follow-up",
        )
    )
    redis_client = _TrackingLocaleRedis()
    redis_client.store["async-group:recent-batch:927331985"] = json.dumps(
        {
            "async_group_id": "group-1",
            "stored_at_ts": 1,
            "legs": [
                {
                    "index": 1,
                    "transaction_id": "tx-1",
                    "task_type": "transfer",
                    "amount": 10000,
                    "recipient_name": "Mum",
                    "recipient_resolved_name": "Mercy Johnson",
                    "recipient_label": "Mercy Johnson",
                    "bank_display": "Opay",
                    "account_display": "8162511023",
                    "final_status": "success",
                    "receipt_allowed": True,
                },
                {
                    "index": 2,
                    "transaction_id": "tx-2",
                    "task_type": "transfer",
                    "amount": 10000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adedayo",
                    "recipient_label": "Tolu Adedayo",
                    "bank_display": "First Bank",
                    "account_display": "0760505261",
                    "final_status": "success",
                    "receipt_allowed": True,
                },
            ],
        }
    )
    state = OrchestratorState(
        user_id="u_gate_receipt_recent_batch",
        phone_number="2348162511023",
        channel="telegram",
        channel_identity="927331985",
        last_message_text="Get me the receipt for the second transaction",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "recent_batch_receipt_support"
    task = updates["tasks"]["direct_support"]
    assert task.type == "support"
    assert task.payload["intent"] == "receipt_request"
    assert task.payload["recent_batch_followup"] is True


async def test_gate_routes_active_receipt_thread_followup_to_support_without_receipt_keyword() -> None:
    redis_client = _TrackingLocaleRedis()
    redis_client.store["support_context:u_gate_receipt_thread_1"] = json.dumps(
        {
            "receipt_thread_state": {
                "async_group_id": "group-1",
                "candidates": [
                    {
                        "transaction_id": "tx-1",
                        "ordinal": 1,
                        "task_type": "transfer",
                        "amount": 10000,
                        "recipient_name": "Mum",
                        "recipient_resolved_name": "Mercy Johnson",
                        "recipient_label": "Mercy Johnson",
                        "bank_display": "Opay",
                        "account_display": "8162511023",
                        "final_status": "success",
                        "receipt_allowed": True,
                    },
                    {
                        "transaction_id": "tx-2",
                        "ordinal": 2,
                        "task_type": "transfer",
                        "amount": 10000,
                        "recipient_name": "Tolu",
                        "recipient_resolved_name": "Tolu Adedayo",
                        "recipient_label": "Tolu Adedayo",
                        "bank_display": "First Bank",
                        "account_display": "0760505261",
                        "final_status": "success",
                        "receipt_allowed": True,
                    },
                ],
                "served_transaction_ids": ["tx-1"],
                "remaining_transaction_ids": ["tx-2"],
                "last_selector_result_ids": ["tx-1"],
                "last_served_transaction_ids": ["tx-1"],
                "reminder": "Reply with 1 or 2.",
            }
        }
    )
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.55,
            detected_language="English",
            response_key="conversational.clarify",
            response=None,
            expected_transaction_executors=[],
            reason="should not run for active receipt-thread follow-up",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_receipt_thread_1",
        phone_number="2348162511023",
        channel="telegram",
        channel_identity="927331985",
        last_message_text="Also for the other one",
        loaded_context={"language": "en", "user_id": "u_gate_receipt_thread_1"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["routing_decision"] == "receipt_thread_support"
    task = updates["tasks"]["direct_support"]
    assert task.type == "support"
    assert task.payload["intent"] == "receipt_request"
    assert task.payload["receipt_thread_followup"] is True


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


async def test_gate_handles_transfer_capability_question_meta_deterministically() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            mode="new",
            target_intent="transfer",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not be used for transfer capability question",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_cap_transfer_1",
        phone_number="2348777777789",
        channel="whatsapp",
        last_message_text="Can you help me send funds?",
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


async def test_gate_explicit_cancel_with_active_state_skips_query_session_lookup() -> None:
    redis_client = _TrackingRedisWithSession(
        '{"session_active": true, "pending_clarification": {"kind": "pending_clarification"}}'
    )
    state = OrchestratorState(
        user_id="u_gate_cancel_fast_1",
        phone_number="2348888888890",
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
    config: RunnableConfig = {"configurable": {"redis_client": redis_client}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_cancelled_prompt("en")
    assert redis_client.query_session_gets == 0
    assert redis_client.deleted_keys == ["query:session:2348888888890"]


async def test_gate_explicit_cancel_dismisses_pending_mandate_notice() -> None:
    state = OrchestratorState(
        user_id="u_gate_cancel_mandate_1",
        phone_number="2348888888891",
        channel="whatsapp",
        last_message_text="Abort",
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "bank_name": "First Bank",
                    "account_number": "0334557890",
                    "mandate_status": "pending",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_cancelled_prompt("en")
    assert updates["routing_decision"] == "cancel_pending_mandate_notice"


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


async def test_gate_active_query_session_preempts_context_frame_followup() -> None:
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
            reason="should not run",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(decision="show_details", confidence=0.96),
    )
    state = OrchestratorState(
        user_id="u_gate_surface_preempts_query_shortcut",
        phone_number="2348999999998",
        channel="whatsapp",
        last_message_text="details",
        loaded_context={"language": "en"},
        stashed_query_session={"session_active": True, "query_result": {"summary_text": "Showing 1-5 of 8"}},
        context_frames=[
            ContextFrame(
                frame_id="surface_tx_details",
                frame_type=ContextFrameType.TRANSACTION_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.TRANSACTION,
                        entity_id="tx-surface-1",
                        label="Transfer to Tolu",
                        data={
                            "amount": 2000,
                            "bank_name": "GTBank",
                            "transaction_type": "debit",
                            "status": "successful",
                        },
                    )
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 0
    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "query_followup_bypass"
    assert updates["routing_owner"] == "query_session"
    assert updates["routing_decision"] == "query_followup_bypass"
    assert updates["routing_target_domain"] == "query"
    assert updates["routing_mode"] == "continuation"
    assert updates.get("waves") == [["direct_query"]]


async def test_gate_latest_fact_next_followup_stays_in_active_query_session() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.99,
            detected_language="English",
            requested_language="Pidgin",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not run",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_next_fact_1",
        phone_number="2348999999910",
        channel="whatsapp",
        last_message_text="Then who next?",
        loaded_context={"language": "en"},
        stashed_query_session={
            "session_active": True,
            "query_contract": {
                "intent": "transaction_search",
                "time_start": "2026-04-01",
                "time_end": "2026-04-10",
                "timezone": "Africa/Lagos",
                "filters": {"transaction_type": "debit"},
                "result_limit": 1,
                "result_reference": "latest",
                "answer_fact_field": "counterparty",
            },
            "query_result": {
                "summary_text": "The last person you sent money to was Mum.",
                "surface_view": {"mode": "direct_answer", "context": {"type": "single_transaction"}},
            },
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "query_followup_bypass"
    assert updates["routing_decision"] == "query_followup_bypass"
    assert updates["routing_mode"] == "continuation"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "Then who next?"


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


async def test_gate_deterministic_account_list_bypasses_semantic_router() -> None:
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

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_account_domain"
    task = updates["tasks"]["direct_account"]
    assert task.type == "account"
    assert task.payload["message"] == "Show my linked accounts"


async def test_gate_deterministic_beneficiary_list_bypasses_semantic_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_beneficiary",
            mode="new",
            target_intent="beneficiary",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="single-domain beneficiary request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_beneficiary_1",
        phone_number="23489999999171",
        channel="whatsapp",
        last_message_text="Show my beneficiaries",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_beneficiary_domain"
    task = updates["tasks"]["direct_beneficiary"]
    assert task.type == "beneficiary"
    assert task.payload["message"] == "Show my beneficiaries"
    assert task.payload["action"] == "list_beneficiaries"
    assert task.payload["intent"] == "list_beneficiaries"
    assert task.payload["list_intent"] is True


async def test_gate_context_frame_completeness_preempts_beneficiary_reroute() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_beneficiary",
            mode="new",
            target_intent="beneficiary",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="would repeat list if frame follow-up did not preempt",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="completeness_check",
            confidence=0.96,
            detected_language="English",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_1",
        phone_number="23489999999174",
        channel="whatsapp",
        last_message_text="Is that all?",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_gate",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Tolu GTB",
                        data={"alias": "Tolu GTB", "account_name": "Tolu Adeyemi"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-3",
                        label="Tolu First",
                        data={"alias": "Tolu First", "account_name": "Tolulope Johnson"},
                    ),
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "context_frame_followup"
    assert updates["final_response"] == "Yes. Those are the 3 saved beneficiaries I found."
    assert "tasks" not in updates


async def test_gate_context_frame_lookup_preempts_beneficiary_reroute() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_beneficiary",
            mode="new",
            target_intent="beneficiary",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="would repeat list if frame follow-up did not preempt",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="entity_lookup",
            confidence=0.96,
            detected_language="English",
            target_text="gaines",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_2",
        phone_number="23489999999175",
        channel="whatsapp",
        last_message_text="What about gaines",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_gate_lookup",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Tolu GTB",
                        data={"alias": "Tolu GTB", "account_name": "Tolu Adeyemi"},
                    ),
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert "Frame type: beneficiary_list" in (planner.last_frame_context or "")
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "context_frame_followup"
    assert updates["final_response"] == "I don't see Gaines in the saved beneficiaries I showed."
    assert "tasks" not in updates


async def test_gate_context_frame_expected_missing_entity_preempts_beneficiary_reroute() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_beneficiary",
            mode="new",
            target_intent="beneficiary",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="would repeat list if expected missing entity follow-up did not preempt",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="lookup_entity",
            confidence=0.96,
            detected_language="English",
            target_text="gaines",
            reason="user expected a named beneficiary in the displayed list",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_expected_missing",
        phone_number="23489999999176",
        channel="whatsapp",
        last_message_text="I thought I had gaines too",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_gate_expected_missing",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Tolu GTB",
                        data={"alias": "Tolu GTB", "account_name": "Tolu Adeyemi"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-3",
                        label="Tolu First",
                        data={"alias": "Tolu First", "account_name": "Tolulope Johnson"},
                    ),
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "context_frame_followup"
    assert updates["final_response"] == "I don't see Gaines in the saved beneficiaries I showed."
    assert updates["context_frames"]
    assert "tasks" not in updates


async def test_gate_context_frame_start_new_task_falls_through_to_fresh_beneficiary_read() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_beneficiary",
            mode="new",
            target_intent="beneficiary",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="would repeat list if uncertain frame decision fell through",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="start_new_task",
            confidence=0.78,
            detected_language="English",
            reason="user is asking for a fresh beneficiary read",
        ),
    )
    created_at = int(time.time()) - 300
    state = OrchestratorState(
        user_id="u_gate_frame_followup_uncertain_fresh_task",
        phone_number="23489999999180",
        channel="whatsapp",
        last_message_text="Show my beneficiaries",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_gate_uncertain_fresh_task",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    )
                ],
                created_at_ts=created_at,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_beneficiary_domain"
    task = updates["tasks"]["direct_beneficiary"]
    assert task.type == "beneficiary"
    assert task.payload["action"] == "list_beneficiaries"
    assert task.payload["list_intent"] is True


async def test_gate_context_frame_does_not_steal_fresh_transfer_request() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_beneficiary",
            mode="new",
            target_intent="beneficiary",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="would show beneficiary details if frame follow-up stole the transfer",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="lookup_entity",
            confidence=0.98,
            detected_language="English",
            target_text="tolu adebayo",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_transfer_fresh",
        phone_number="23489999999181",
        channel="whatsapp",
        last_message_text="Send 10k to tolu adebayo",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_gate_transfer_fresh",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={
                            "alias": "Tolu Access",
                            "account_name": "Tolu Adebayo",
                            "bank_name": "Access Bank",
                            "account_number": "2010000001",
                        },
                    )
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 0
    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_transfer_domain"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "fresh_transfer_command"
    task = updates["tasks"]["direct_transfer"]
    assert task.type == "transfer"
    assert task.payload["message"] == "Send 10k to tolu adebayo"


async def test_gate_context_frame_unclear_followup_returns_frame_specific_clarification() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.4,
            detected_language="English",
            expected_transaction_executors=[],
            reason="would produce generic fallback if frame stage did not clarify",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="unclear",
            confidence=0.72,
            detected_language="English",
            reason="ambiguous but likely related to visible frame",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_unclear_beneficiary",
        phone_number="23489999999179",
        channel="whatsapp",
        last_message_text="I thought I had something else too",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_gate_unclear",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    )
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert updates["semantic_path_shape"] == "context_frame_followup"
    assert updates["final_response"] == "Are you asking about the saved beneficiaries I just showed?"
    assert "tasks" not in updates


async def test_gate_context_frame_filter_operation_preempts_account_reroute() -> None:
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
            reason="would route to account domain if frame filter did not preempt",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.94,
            detected_language="English",
            target_text="gtbank",
            reason="user wants only the GTBank item from the displayed frame",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_filter_account",
        phone_number="23489999999177",
        channel="whatsapp",
        last_message_text="Which one is GTBank?",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="accounts_recent_gate_filter",
                frame_type=ContextFrameType.ACCOUNT_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.ACCOUNT,
                        entity_id="acct-1",
                        label="First Bank (...0001)",
                        data={"bank_name": "First Bank", "account_number": "6000000001"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.ACCOUNT,
                        entity_id="acct-2",
                        label="GTBank (...0002)",
                        data={"bank_name": "GTBank", "account_number": "6000000002"},
                    ),
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert updates["semantic_path_shape"] == "context_frame_followup"
    assert "GTBank (...0002)" in updates["final_response"]
    assert "First Bank (...0001)" not in updates["final_response"]
    assert "tasks" not in updates


async def test_gate_context_frame_compare_operation_answers_from_frame() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.4,
            detected_language="English",
            expected_transaction_executors=[],
            reason="would be ambiguous without frame comparison",
        ),
        frame_followup_decision=ContextFrameFollowupDecision(
            decision="compare_items",
            confidence=0.92,
            detected_language="English",
            reason="user wants to compare the displayed account items",
        ),
    )
    state = OrchestratorState(
        user_id="u_gate_frame_followup_compare_account",
        phone_number="23489999999178",
        channel="whatsapp",
        last_message_text="Compare them",
        loaded_context={"language": "en"},
        context_frames=[
            ContextFrame(
                frame_id="accounts_recent_gate_compare",
                frame_type=ContextFrameType.ACCOUNT_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.ACCOUNT,
                        entity_id="acct-1",
                        label="First Bank (...0001)",
                        data={"bank_name": "First Bank", "account_number": "6000000001", "balance": 20000},
                    ),
                    ContextEntity(
                        entity_type=EntityType.ACCOUNT,
                        entity_id="acct-2",
                        label="GTBank (...0002)",
                        data={"bank_name": "GTBank", "account_number": "6000000002", "balance": 30000},
                    ),
                ],
                created_at_ts=int(time.time()),
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.frame_followup_calls == 1
    assert planner.route_calls == 0
    assert updates["semantic_path_shape"] == "context_frame_followup"
    assert "Comparison" in updates["final_response"]
    assert "First Bank (...0001)" in updates["final_response"]
    assert "GTBank (...0002)" in updates["final_response"]
    assert "Balance: 30000" in updates["final_response"]
    assert "tasks" not in updates


async def test_gate_deterministic_airtime_bypasses_semantic_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_airtime",
            mode="new",
            target_intent="airtime",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["airtime"],
            reason="single-domain airtime request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_airtime_1",
        phone_number="23489999999172",
        channel="whatsapp",
        last_message_text="Buy 2k airtime for 08031234567",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_airtime_domain"
    task = updates["tasks"]["direct_airtime"]
    assert task.type == "airtime"


async def test_gate_deterministic_data_bypasses_semantic_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_data",
            mode="new",
            target_intent="data",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["data"],
            reason="single-domain data request",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_data_1",
        phone_number="23489999999173",
        channel="whatsapp",
        last_message_text="Buy 1gb for me",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_data_domain"
    task = updates["tasks"]["direct_data"]
    assert task.type == "data"


async def test_gate_banking_coded_transfer_ambiguity_clarifies_before_casual_chat() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.82,
            detected_language="English",
            response_key="conversational.casual_chat",
            response="",
            expected_transaction_executors=[],
            reason="should not win against banking ambiguity guard",
        )
    )
    responder = _FakeConversationResponder("This should not be used.")
    state = OrchestratorState(
        user_id="u_gate_router_transfer_ambiguous_1",
        phone_number="23489999999174",
        channel="whatsapp",
        last_message_text="Pay me tithe",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "conversation_responder": responder},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "banking_coded_ambiguity_clarify"
    assert updates["final_response"] == "Do you want to send money? If yes, who is the recipient?"
    assert updates["routing_decision"] == "banking_coded_ambiguity_transfer"
    assert not responder.calls


async def test_gate_banking_coded_data_ambiguity_clarifies_before_direct_data_route() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_data",
            mode="new",
            target_intent="data",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["data"],
            reason="should not run for malformed banking-coded data ask",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_data_ambiguous_1",
        phone_number="23489999999175",
        channel="whatsapp",
        last_message_text="Buy me data",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "banking_coded_ambiguity_clarify"
    assert updates["final_response"] == "Do you want to buy data? If yes, whose line is it for?"
    assert updates["routing_decision"] == "banking_coded_ambiguity_data"


async def test_gate_banking_coded_support_ambiguity_clarifies_before_casual_chat() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.79,
            detected_language="English",
            response_key="conversational.out_of_scope",
            response="I can't help with that.",
            expected_transaction_executors=[],
            reason="should not win against banking ambiguity guard",
        )
    )
    responder = _FakeConversationResponder("This should not be used.")
    state = OrchestratorState(
        user_id="u_gate_router_support_ambiguous_1",
        phone_number="23489999999176",
        channel="whatsapp",
        last_message_text="Reverse me that payment",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "conversation_responder": responder},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "banking_coded_ambiguity_clarify"
    assert updates["final_response"] == "Which transaction do you want me to check?"
    assert updates["routing_decision"] == "banking_coded_ambiguity_support"
    assert not responder.calls


async def test_gate_routes_failed_last_transaction_to_support_through_semantic_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_support",
            mode="new",
            confidence=0.91,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="transaction support issue",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_support_issue_1",
        phone_number="23489999999177",
        channel="whatsapp",
        last_message_text="My last transaction failed",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.last_context is not None
    assert "candidate_domain=support" in planner.last_context
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    assert updates["routing_owner"] == "semantic_router"
    assert updates["routing_target_domain"] == "support"
    assert updates["routing_decision"] == "domain_support"
    task = updates["tasks"]["direct_support"]
    assert task.type == "support"
    assert task.payload["message"] == "My last transaction failed"


async def test_gate_routes_debited_not_received_to_support_through_semantic_router() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_support",
            mode="new",
            confidence=0.91,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="transaction support issue",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_support_issue_2",
        phone_number="23489999999178",
        channel="whatsapp",
        last_message_text="I was debited but they didn't receive it",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.last_context is not None
    assert "candidate_domain=support" in planner.last_context
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_support"]
    assert task.type == "support"


async def test_gate_support_issue_falls_back_to_direct_when_semantic_router_unavailable() -> None:
    state = OrchestratorState(
        user_id="u_gate_support_issue_no_router",
        phone_number="23489999999181",
        channel="whatsapp",
        last_message_text="My last transaction failed",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "support_issue_direct"
    assert updates["routing_decision"] == "support_issue_direct"
    task = updates["tasks"]["direct_support"]
    assert task.type == "support"


async def test_gate_semantic_v2_support_issue_uses_router_hint() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_support",
            mode="new",
            confidence=0.91,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="transaction support issue",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_support_issue_v2_1",
        phone_number="23489999999179",
        channel="whatsapp",
        last_message_text="My last transaction failed",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.last_context is not None
    assert "candidate_domain=support" in planner.last_context
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    assert updates["routing_owner"] == "semantic_router"
    assert updates["routing_target_domain"] == "support"
    assert updates["routing_decision"] == "domain_support"
    task = updates["tasks"]["direct_support"]
    assert task.type == "support"


async def test_gate_semantic_v2_support_issue_vetoes_query_route() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            confidence=0.88,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="incorrect query route",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_support_issue_v2_2",
        phone_number="23489999999180",
        channel="whatsapp",
        last_message_text="My last transaction failed",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert updates["semantic_path_shape"] == "support_hint_planner_handoff"
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "support_hint_planner_handoff"
    assert updates["routing_heuristic_type"] == "routing_hint"
    assert updates["routing_heuristic_name"] == "support_issue_phrase"


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


async def test_gate_amount_only_transfer_fastpath_still_routes_to_transfer_worker() -> None:
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
            reason="amount-only transfer start",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_2",
        phone_number="23489999999181",
        channel="whatsapp",
        last_message_text="Send 10k",
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
    assert updates["routing_decision"] == "fresh_transfer_missing_recipient_command"
    task = updates["tasks"]["direct_transfer"]
    assert task.type == "transfer"
    assert task.payload["message"] == "Send 10k"


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

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "batch_transfer_command"


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

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "batch_transfer_command"


async def test_gate_multi_amount_transfer_turn_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="multi recipient transfer requires decomposition",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_batch_4",
        phone_number="2348999999922",
        channel="whatsapp",
        last_message_text="Send 12k to mum and 6k to gaines",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "batch_transfer_command"


async def test_gate_multi_recipient_transfer_turn_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="multi recipient transfer requires decomposition",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_router_transfer_batch_5",
        phone_number="2348999999923",
        channel="whatsapp",
        last_message_text="Send 10k to tolu and mum",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "batch_transfer_command"


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

    assert planner.route_calls == 0
    assert planner.plan_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert "tasks" not in updates
    assert "turn_context_summary" in updates
    assert updates["preplanner_expected_transaction_executors"] == ["transfer"]
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_target_domain"] == "transfer"
    assert updates["routing_decision"] == "account_aware_transfer_command"


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


async def test_gate_ignores_semantic_router_locale_switch_hallucination_for_query_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_query",
            mode="new",
            target_intent="query",
            confidence=0.87,
            detected_language="English",
            requested_language="Pidgin",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="hallucinated locale switch",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_locale_guard_1",
        phone_number="2348000000011",
        channel="whatsapp",
        last_message_text="Show my recent transactions",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "redis_client": None}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_domain"
    task = updates["tasks"]["direct_query"]
    assert task.type == "query"
    assert task.payload["message"] == "Show my recent transactions"
    assert updates.get("final_response") is None


async def test_gate_handles_explicit_language_switch_deterministically_before_semantic_router(monkeypatch) -> None:
    redis_client = _TrackingLocaleRedis()
    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: redis_client))

    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.2,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="should not run for deterministic language switch",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_switch_det",
        phone_number="2348000000007",
        channel="whatsapp",
        last_message_text="switch to pidgin",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "pcm"
    assert updates["final_response"] == render_locale_switched("pcm")
    assert redis_client.store["user:2348000000007:language"] == "pcm"
    assert redis_client.store["user:2348000000007:language_explicit"] == "1"


async def test_gate_english_domain_fastpath_still_applies_with_non_english_locale() -> None:
    state = OrchestratorState(
        user_id="u_gate_en_fastpath",
        phone_number="2348000000008",
        channel="whatsapp",
        last_message_text="show my accounts",
        loaded_context={"language": "pcm"},
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_account_domain"
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "deterministic_account_domain"


async def test_gate_non_english_domain_phrase_falls_through_safely(monkeypatch) -> None:
    redis_client = _TrackingLocaleRedis()
    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: redis_client))

    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.72,
            detected_language="Yoruba",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="needs semantic fallback",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_non_en_fallback",
        phone_number="2348000000009",
        channel="whatsapp",
        last_message_text="fihan mi awon beneficiary mi",
        loaded_context={"language": "yo"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert not updates.get("direct_path_triggered", False)
    assert "tasks" not in updates
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_handoff"


async def test_gate_semantic_router_direct_reply_does_not_override_explicit_locale(monkeypatch) -> None:
    redis_client = _TrackingLocaleRedis()
    redis_client.store["user:2348000000010:language"] = "pcm"
    redis_client.store["user:2348000000010:language_explicit"] = "1"
    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: redis_client))

    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.97,
            detected_language="English",
            response_key="conversational.greeting",
            response=None,
            expected_transaction_executors=[],
            reason="greeting",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_explicit_locale",
        phone_number="2348000000010",
        channel="whatsapp",
        last_message_text="Hi",
        loaded_context={"language": "pcm"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": redis_client},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == render_message("conversational.greeting", "pcm")
    assert "loaded_context" not in updates or updates["loaded_context"]["language"] == "pcm"


class _RouteTurnPlanner:
    def __init__(
        self,
        decision: SemanticRouteDecision,
        *,
        frame_followup_decision: ContextFrameFollowupDecision | None = None,
    ) -> None:
        self._decision = decision
        self._frame_followup_decision = frame_followup_decision
        self.route_calls = 0
        self.frame_followup_calls = 0
        self.plan_calls = 0
        self.last_context: str | None = None
        self.last_frame_context: str | None = None

    async def route_semantic_turn(self, phone_number: str, text: str, context: str = "None") -> SemanticRouteDecision:
        del phone_number, text
        self.route_calls += 1
        self.last_context = context
        return self._decision

    async def interpret_context_frame_followup(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "direct_path",
    ) -> ContextFrameFollowupDecision:
        del phone_number, text, path_label
        self.frame_followup_calls += 1
        self.last_frame_context = context
        if self._frame_followup_decision is None:
            return ContextFrameFollowupDecision(decision="new_task", confidence=0.99)
        return self._frame_followup_decision

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

    async def get(self, key: str) -> str | None:
        return self.store.get(key)


class _TrackingRedisWithSession(_TrackingRedis):
    def __init__(self, payload: str | None) -> None:
        super().__init__()
        self.payload = payload
        self.query_session_gets = 0

    async def get(self, key: str) -> str | None:
        if "query:session:" in key:
            self.query_session_gets += 1
            return self.payload
        return None


class _FakeConversationResponder:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    async def generate_reply(
        self,
        phone_number: str,
        text: str,
        user_ctx: dict[str, object],
        intent: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "phone_number": phone_number,
                "text": text,
                "user_ctx": dict(user_ctx),
                "intent": intent,
            }
        )
        return self.reply


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


async def test_gate_semantic_router_context_omits_account_and_beneficiary_previews_when_not_needed() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_ambiguous",
            confidence=0.62,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="ambiguous non-entity turn",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_ctx_trim_1",
        phone_number="23480000000051",
        channel="whatsapp",
        last_message_text="I need help with this request",
        loaded_context={
            "language": "en",
            "accounts": [{"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "8162511023"}],
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert planner.last_context is not None
    assert "ACCOUNTS:" not in planner.last_context
    assert "BENEFICIARIES:" not in planner.last_context
    assert updates["routing_decision"] == "planner_handoff"


async def test_gate_semantic_router_missing_reply_for_non_banking_turn_falls_back_to_redirect(
    monkeypatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.nodes.gate.runner.logger.info", _capture)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.nodes.gate.pipeline.semantic_router_stage.logger.info", _capture)

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
    assert updates["final_response"] == render_message("conversational.out_of_scope", "en")
    assert not any(event == "unexpected_turn_route_breadcrumb" for event, _ in events)


async def test_gate_semantic_router_missing_reply_uses_conversation_responder_for_non_banking_turn() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.83,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="casual non-banking turn",
        )
    )
    responder = _FakeConversationResponder(
        "Today is Thursday, April 09, 2026.\nI can still help with transfers, airtime/data, balances, and transaction history."
    )
    state = OrchestratorState(
        user_id="u_gate_conv_1",
        phone_number="23480000000052",
        channel="whatsapp",
        last_message_text="What's today's date",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "conversation_responder": responder},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_direct"
    assert updates["final_response"] == responder.reply
    assert responder.calls
    assert responder.calls[0]["intent"] == "non_banking_conversational"


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

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "direct_context_recap"
    assert updates["final_response"] == "We are still in your transfer flow. Continue with that flow."


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


async def test_gate_semantic_router_casual_chat_uses_conversation_responder_when_available() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.82,
            detected_language="English",
            response_key="conversational.casual_chat",
            response="",
            expected_transaction_executors=[],
            reason="harmless non-banking turn",
        )
    )
    responder = _FakeConversationResponder(
        "Small one: bankers love balance because it always checks out.\nI can handle transfers, airtime/data, balances, and transaction history."
    )
    state = OrchestratorState(
        user_id="u_gate_oos_conv_1",
        phone_number="23480000000059",
        channel="whatsapp",
        last_message_text="Tell me about joke",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "conversation_responder": responder},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "semantic_router_direct"
    assert updates["final_response"] == responder.reply
    assert responder.calls
    assert responder.calls[0]["intent"] == "non_banking_conversational"


async def test_gate_semantic_router_generic_banking_refusal_out_of_scope_uses_conversation_responder() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.82,
            detected_language="English",
            response_key="conversational.out_of_scope",
            response="I can't help with jokes, but I can assist with your banking tasks.",
            expected_transaction_executors=[],
            reason="harmless non-banking turn misclassified as out-of-scope",
        )
    )
    responder = _FakeConversationResponder(
        "Small one: bankers love balance because it always checks out.\nI can handle transfers, airtime/data, balances, and transaction history."
    )
    state = OrchestratorState(
        user_id="u_gate_oos_conv_2",
        phone_number="23480000000060",
        channel="whatsapp",
        last_message_text="Tell me a joke",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "conversation_responder": responder},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert updates["final_response"] == responder.reply
    assert responder.calls


async def test_gate_contextual_casual_followup_bypasses_semantic_router_to_responder() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="direct_context_answer",
            confidence=0.61,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="ambiguous follow-up",
        )
    )
    responder = _FakeConversationResponder(
        render_message("conversational.out_of_scope_firm", "en")
    )
    state = OrchestratorState(
        user_id="u_gate_oos_conv_followup_1",
        phone_number="23480000000061",
        channel="whatsapp",
        last_message_text="One more",
        loaded_context={
            "language": "en",
            "history": [
                {"role": "user", "content": "Tell me a joke"},
                {
                    "role": "assistant",
                    "content": "Why did the savings account blush? Because it saw its balance growing.\n"
                    + render_message("conversational.out_of_scope", "en"),
                },
                {"role": "user", "content": "Another one"},
                {
                    "role": "assistant",
                    "content": "Sure — my wallet is on a strict budget.\n"
                    + render_message("conversational.out_of_scope_followup", "en"),
                },
            ],
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "conversation_responder": responder},
        "recursion_limit": 50,
    }

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "contextual_casual_followup"
    assert updates["final_response"] == responder.reply
    assert responder.calls


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


async def test_gate_semantic_router_single_domain_miss_on_obvious_mixed_transfer_airtime_falls_through_to_planner() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_airtime",
            mode="new",
            target_intent="airtime",
            confidence=0.93,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["airtime"],
            reason="router single-domain miss",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_mixed_tx_veto_1",
        phone_number="23480000000061",
        channel="whatsapp",
        last_message_text="send 10k to mum and buy me 2k airtime",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 1
    assert updates.get("direct_path_triggered") is None
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_handoff"
    assert updates["preplanner_expected_transaction_executors"] == ["transfer", "airtime"]
    assert "tasks" not in updates


async def test_gate_skips_semantic_router_for_live_pending_interrupt() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="planner_mixed",
            confidence=0.91,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer", "airtime"],
            reason="unused for interrupt follow-up",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_interrupt_1",
        phone_number="2348000000006",
        channel="whatsapp",
        last_message_text="make it 20k",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"]),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"amount": 10000, "recipient_name": "Mum"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert updates["routing_decision"] == "planner_handoff"


async def test_gate_skips_semantic_router_for_numeric_input_source_selection_interrupt() -> None:
    planner = _RouteTurnPlanner(
        SemanticRouteDecision(
            decision="domain_transfer",
            confidence=0.92,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=["transfer"],
            reason="unused for numeric source-account reply",
        )
    )
    state = OrchestratorState(
        user_id="u_gate_interrupt_input_1",
        phone_number="23480000000061",
        channel="whatsapp",
        last_message_text="1",
        loaded_context={"language": "en"},
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["source_account_id"]},
            prompt="Which account should I use?",
        ),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={"amount": 30000, "recipient_name": "Mum"},
            )
        },
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_direct_path(state, config)

    assert planner.route_calls == 0
    assert updates.get("direct_path_triggered") is None
    assert updates["routing_decision"] == "planner_handoff"


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

    assert planner.route_calls == 0
    assert updates["direct_path_triggered"] is True
    assert updates["semantic_path_shape"] == "deterministic_account_domain"
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

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.nodes.gate.runner.logger.info", _capture)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.nodes.gate.pipeline.semantic_router_stage.logger.info", _capture)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.nodes.gate.pipeline.domain_stages.logger.info", _capture)

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
