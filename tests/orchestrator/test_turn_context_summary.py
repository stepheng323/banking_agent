from __future__ import annotations

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    _load_query_session_snapshot,
    build_router_context_from_summary,
    build_turn_context_summary,
    build_user_state_summary_from_summary,
)


async def test_load_query_session_snapshot_prefers_redis_then_stashed() -> None:
    class _Redis:
        async def get(self, key: str) -> str:
            assert key == "query:session:2348000000301"
            return '{"session_active": true, "query_result": {"summary_text": "You spent ₦5,000 today."}}'

    state = OrchestratorState(
        user_id="u_ctx_redis",
        phone_number="2348000000301",
        channel="whatsapp",
        stashed_query_session={"session_active": True, "query_result": {"summary_text": "stashed"}},
    )

    snapshot, source = await _load_query_session_snapshot(state, _Redis())

    assert source == "redis"
    assert snapshot is not None
    assert snapshot["query_result"]["summary_text"] == "You spent ₦5,000 today."


def test_turn_context_summary_builds_compact_shared_view() -> None:
    state = OrchestratorState(
        user_id="u_ctx_1",
        phone_number="2348000000302",
        channel="telegram",
        active_domain="account",
        loaded_context={
            "profile": {"first_name": "Gaines", "last_name": "Doe"},
            "accounts": [
                {"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready", "is_default": True},
                {
                    "bank_name": "First Bank",
                    "account_number": "0334557890",
                    "mandate_status": "pending",
                    "extra_data": {
                        "transfer_destinations": [{"bank_name": "NIBSS Bank", "account_number": "0001112223"}]
                    },
                },
            ],
            "beneficiaries": [
                {"alias": "Mum", "bank_name": "Opay", "account_number": "8162511023"},
                {"alias": "Gaines", "bank_name": "Access Bank", "account_number": "0760505262"},
            ],
            "history": [
                {"role": "user", "content": "Show my linked accounts"},
                {"role": "assistant", "content": "Your Bank Accounts\n1. Zenith Bank\n2. First Bank"},
            ],
        },
        context_frames=[
            ContextFrame(
                frame_id="accounts_1",
                frame_type=ContextFrameType.ACCOUNT_LIST,
                items=[ContextEntity(entity_type=EntityType.ACCOUNT, label="First Bank")],
                created_at_ts=9999999999,
            )
        ],
        session_stack=[ActiveSession(domain="account", state="RUNNING", interrupt_policy="ALLOW")],
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["beneficiary_id"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"action": "send_money", "recipient_name": "Tolu", "amount": 10000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )

    summary = build_turn_context_summary(
        state,
        query_session_snapshot={"session_active": True, "query_result": {"summary_text": "You spent ₦5,000 today."}},
        query_session_source="redis",
    )

    assert summary.profile_name == "Gaines Doe"
    assert summary.active_domain == "account"
    assert summary.session_domain == "account"
    assert summary.recent_answer_focus == "linked_accounts_summary"
    assert summary.query_session_active is True
    assert summary.query_session_source == "redis"
    assert summary.query_session_summary is not None
    assert "You spent ₦5,000 today." in summary.query_session_summary
    assert summary.active_flow_intent == "transfer"
    assert summary.active_flow_interrupt_kind == "input"
    assert summary.active_flow_missing_fields == ["beneficiary_id"]
    assert summary.active_flow_summary is not None
    assert "Current Task Data:" in summary.active_flow_summary
    assert any("Zenith Bank" in line and "ready" in line for line in summary.account_lines)
    assert any("First Bank" in line and "Activate: ₦50" in line for line in summary.account_lines)
    assert any("Mum" in line for line in summary.beneficiary_lines)


def test_router_and_user_state_render_from_shared_summary() -> None:
    state = OrchestratorState(
        user_id="u_ctx_2",
        phone_number="2348000000303",
        channel="whatsapp",
        active_domain="query",
        loaded_context={
            "accounts": [{"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "8162511023"}],
            "history": [{"role": "assistant", "content": "You spent ₦5,000 today."}],
        },
    )
    summary = build_turn_context_summary(state)

    router_context = build_router_context_from_summary(summary, expected_executors=["transfer"])
    user_state_summary = build_user_state_summary_from_summary(summary)

    assert "ACTIVE_DOMAIN=query" in router_context
    assert "EXPECTED_TRANSACTION_EXECUTORS=transfer" in router_context
    assert "ACCOUNTS:" in router_context
    assert "BENEFICIARIES:" in router_context
    assert "RECENT_CHAT:" in router_context or "RECENT_CONTEXT:" in router_context
    assert "EXPECTED_TRANSACTION_EXECUTORS=transfer" in router_context
    assert user_state_summary is not None
    assert "User State:" in user_state_summary
    assert "mandate:" in user_state_summary
    assert "Beneficiaries:" in user_state_summary
