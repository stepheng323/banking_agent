"""Fast-path gate tests for conversational i18n behavior."""

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.gate import session_gate_fastpath
from shared.i18n import render_cancelled_prompt, render_message
from shared.types.planner import TurnRouteDecision


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

    updates = await session_gate_fastpath(state, config)
    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == render_cancelled_prompt("en")
    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0


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


async def test_gate_handles_explicit_locale_switch_before_planner() -> None:
    state = OrchestratorState(
        user_id="u_gate_4",
        phone_number="2348000000004",
        channel="whatsapp",
        last_message_text="switch to pidgin",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"redis_client": None}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)

    assert updates["fast_path_triggered"] is True
    assert updates["loaded_context"]["language"] == "pcm"
    assert updates["loaded_context"]["detected_language"] == "pcm"
    assert isinstance(updates.get("final_response"), str)


class _RouteTurnPlanner:
    def __init__(self, decision: TurnRouteDecision) -> None:
        self._decision = decision
        self.route_calls = 0
        self.plan_calls = 0
        self.last_context: str | None = None

    async def route_turn(self, phone_number: str, text: str, context: str = "None") -> TurnRouteDecision:
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


async def test_gate_turn_router_can_bypass_planner_with_direct_response() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
            decision="respond_directly",
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
        last_message_text="how far",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert updates["fast_path_triggered"] is True
    assert isinstance(updates.get("final_response"), str)
    assert updates["loaded_context"]["language"] == "pcm"


async def test_gate_turn_router_can_answer_grounded_account_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "ACCOUNTS:" in (planner.last_context or "")
    assert "First Bank" in (planner.last_context or "")
    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == "Your First Bank account is linked, but it is not ready for payments yet."


async def test_gate_turn_router_can_answer_grounded_account_follow_up_with_typo() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == "Your First Bank account is linked, but it is not ready for payments yet."


async def test_gate_turn_router_can_answer_grounded_beneficiary_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "BENEFICIARIES:" in (planner.last_context or "")
    assert "Mum" in (planner.last_context or "")
    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == "Yes, you still have Mum saved on Opay ending in 1023."


async def test_gate_turn_router_can_answer_grounded_query_follow_up_without_planner() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "QUERY_SESSION:" in (planner.last_context or "")
    assert "3 debits" in (planner.last_context or "")
    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == "Yes. The transactions shown after that include more debits."


async def test_gate_turn_router_can_answer_grounded_flow_recap_without_planner() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert planner.plan_calls == 0
    assert "ACTIVE_FLOW:" in (planner.last_context or "")
    assert "Current Task Data:" in (planner.last_context or "")
    assert updates["fast_path_triggered"] is True
    assert (
        updates["final_response"]
        == "We are on your transfer. I still have your amount and recipient, and the flow is waiting to continue from there."
    )


async def test_gate_turn_router_out_of_scope_includes_empathy_and_redirect() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
            decision="respond_directly",
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

    updates = await session_gate_fastpath(state, config)

    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == "I hear you.\n" + render_message("conversational.out_of_scope", "en")


async def test_gate_turn_router_out_of_scope_without_empathy_uses_redirect_only() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
            decision="respond_directly",
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

    updates = await session_gate_fastpath(state, config)

    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == render_message("conversational.out_of_scope", "en")


async def test_gate_turn_router_passes_expected_executors_without_fastpath() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
            decision="go_planner",
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert updates.get("fast_path_triggered") is None
    assert updates["preplanner_expected_transaction_executors"] == ["transfer", "airtime"]


async def test_gate_fast_path_routes_balance_request_without_turn_router() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
            decision="query_continuation",
            confidence=0.95,
            detected_language="English",
            response_key=None,
            response=None,
            expected_transaction_executors=[],
            reason="misrouted continuation",
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 0
    assert updates["fast_path_triggered"] is True
    assert updates["waves"] == [["fast_account_balance"]]
    task = updates["tasks"]["fast_account_balance"]
    assert task.type == "account"
    assert task.payload["action"] == "check_balance"


async def test_gate_balance_fastpath_does_not_swallow_mixed_transaction_and_balance_request() -> None:
    state = OrchestratorState(
        user_id="u_gate_7b",
        phone_number="23480000000071",
        channel="whatsapp",
        last_message_text="Send 10k to gaines, buy 1k airtime to my line, and show my balance",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)

    assert updates == {}


async def test_gate_turn_router_cancel_response_clears_query_state() -> None:
    planner = _RouteTurnPlanner(
        TurnRouteDecision(
            decision="respond_directly",
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

    updates = await session_gate_fastpath(state, config)

    assert planner.route_calls == 1
    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == render_cancelled_prompt("en")
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["current_wave_index"] == 0
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert updates["stashed_query_session"] is None
    assert redis_client.deleted_keys == ["query:session:2348000000008"]


async def test_gate_explicit_cancel_without_active_state_returns_clarify() -> None:
    state = OrchestratorState(
        user_id="u_gate_8b",
        phone_number="2348000000018",
        channel="whatsapp",
        last_message_text="abort",
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {"configurable": {"redis_client": None}, "recursion_limit": 50}

    updates = await session_gate_fastpath(state, config)

    assert updates["fast_path_triggered"] is True
    assert updates["final_response"] == render_message("conversational.clarify", "en")


async def test_gate_fast_path_cancel_and_balance_cleans_query_and_runs_balance() -> None:
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

    updates = await session_gate_fastpath(state, config)

    assert updates["fast_path_triggered"] is True
    assert updates["waves"] == [["fast_account_balance"]]
    task = updates["tasks"]["fast_account_balance"]
    assert task.type == "account"
    assert task.payload["action"] == "check_balance"
    assert updates["session_stack"] == []
    assert updates["active_domain"] is None
    assert updates["stashed_query_session"] is None
    assert redis_client.deleted_keys == ["query:session:2348000000009"]
