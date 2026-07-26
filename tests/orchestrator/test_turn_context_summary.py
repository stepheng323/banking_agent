from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal

import pytest

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.query_session.context_query_session import (
    _load_query_session_snapshot,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_active import (
    build_interrupt_context_from_summary,
    build_quoted_replay_context_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_core import (
    INTERRUPT_CONTEXT_MAX_CHARS,
    QUOTED_REPLAY_CONTEXT_MAX_CHARS,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_router import (
    build_router_context_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_user import (
    build_user_state_summary_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary import (
    build_turn_context_summary,
    get_or_build_turn_context_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary_payload import (
    _build_history_lines,
    _compact_payload_for_prompt,
    _compact_prompt_value,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary_state import (
    summary_to_state_payload,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import planner_state_view
from banking.transactions.query.models.domain import QueryIntent, TimeRange
from banking.transactions.query.models.extraction import (
    Ambiguity,
    AmbiguityCode,
    PendingClarificationState,
    QueryExtractionResult,
)
from tests.query.factories import make_query_request


def _pending_query_clarification_snapshot(*, timestamp: float | None = None) -> dict[str, object]:
    query_request = make_query_request(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 31)),
    )
    return {
        "session_active": True,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "pending_clarification": {
            "original_query": "How much did I spend last?",
            "resolver_message": "Which period did you mean?",
        },
        "query_request": query_request.model_dump(mode="json"),
    }


def _query_surface_frame(*, summary_text: str = "Netflix was ₦5,000.") -> ContextFrame:
    contract = (
        make_query_request(
            intent=QueryIntent.TRANSACTION_SEARCH,
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 28)),
            result_limit=1,
        )
    )
    return ContextFrame(
        frame_id="query_surface_1",
        frame_type=ContextFrameType.TRANSACTION_DETAIL,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="txn-1",
                label="Netflix",
            )
        ],
        created_at_ts=int(time.time()),
        metadata={
            "source": "query",
            "surface_mode": "direct_answer",
            "summary_text": summary_text,
            "query_request": contract.model_dump(mode="json"),
            "surface_context": {"mode": "direct_answer", "type": "single_transaction"},
        },
    )


async def test_load_query_session_snapshot_uses_pending_clarification_when_no_context_frame() -> None:
    class _Redis:
        async def get(self, key: str) -> str:
            raise AssertionError(f"Redis should not be queried for planner session state: {key}")

    state = OrchestratorState(
        user_id="u_ctx_stashed",
        phone_number="2348000000301",
        channel="whatsapp",
        pending_query_clarification=_pending_query_clarification_snapshot(),
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source == "pending_clarification"
    assert snapshot is not None
    assert snapshot["session_active"] is True
    assert "query_result" not in snapshot


async def test_load_query_session_snapshot_ignores_redis_when_no_orchestrator_context() -> None:
    class _Redis:
        async def get(self, key: str) -> str:
            raise AssertionError(f"Redis should not be queried for planner session state: {key}")

    state = OrchestratorState(
        user_id="u_ctx_no_redis",
        phone_number="2348000000301",
        channel="whatsapp",
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source is None
    assert snapshot is None


async def test_load_query_session_snapshot_prefers_context_frame_over_pending_clarification() -> None:
    class _Redis:
        async def get(self, key: str) -> str:
            raise AssertionError(f"Redis should not be queried for planner session state: {key}")

    state = OrchestratorState(
        user_id="u_ctx_frame",
        phone_number="2348000000301",
        channel="whatsapp",
        context_frames=[_query_surface_frame()],
        pending_query_clarification=_pending_query_clarification_snapshot(),
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source == "context_frame"
    assert snapshot is not None
    assert snapshot["query_result"]["summary_text"] == "Netflix was ₦5,000."


async def test_load_query_session_snapshot_no_longer_prefers_redis() -> None:
    state = OrchestratorState(
        user_id="u_ctx_redis",
        phone_number="2348000000301",
        channel="whatsapp",
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source is None
    assert snapshot is None


def test_compact_payload_for_prompt_serializes_decimal_amounts() -> None:
    assert _compact_prompt_value(Decimal("2000.00")) == "2000.00"

    preview = _compact_payload_for_prompt(
        {
            "action": "buy_airtime",
            "amount": Decimal("2000.00"),
            "nested": {"refund_amount": Decimal("50.25")},
        }
    )

    decoded = json.loads(preview)
    assert decoded["amount"] == "2000.00"
    assert decoded["nested"]["refund_amount"] == "50.25"


async def test_load_query_session_snapshot_ignores_stale_redis_session() -> None:
    class _Redis:
        async def get(self, key: str) -> str:
            raise AssertionError(f"Redis should not be queried for planner session state: {key}")

    state = OrchestratorState(
        user_id="u_ctx_redis_stale",
        phone_number="2348000000306",
        channel="whatsapp",
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source is None
    assert snapshot is None


@pytest.mark.asyncio
async def test_load_query_session_snapshot_marks_stale_pending_clarification_inactive() -> None:
    state = OrchestratorState(
        user_id="u_ctx_stashed_stale",
        phone_number="2348000000307",
        channel="whatsapp",
        pending_query_clarification=_pending_query_clarification_snapshot(timestamp=time.time() - 301),
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source == "pending_clarification"
    assert snapshot is not None
    assert snapshot["session_active"] is False
    assert "query_result" not in snapshot


@pytest.mark.asyncio
async def test_load_query_session_snapshot_logs_session_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.workflows.planner.context.query_session.context_query_session.logger.info",
        _capture,
    )

    state = OrchestratorState(
        user_id="u_ctx_log_shape",
        phone_number="2348000000313",
        channel="whatsapp",
        pending_query_clarification=_pending_query_clarification_snapshot(),
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source == "pending_clarification"
    assert snapshot is not None
    assert (
        "planner_query_session_snapshot",
        {
            "query_session_source": "pending_clarification",
            "session_active": True,
            "has_query_request": True,
            "has_query_result": False,
            "has_surface": False,
            "has_query_frames": False,
        },
    ) in events


@pytest.mark.asyncio
async def test_load_query_session_snapshot_logs_typed_surface_shape_without_legacy_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.workflows.planner.context.query_session.context_query_session.logger.info",
        _capture,
    )

    state = OrchestratorState(
        user_id="u_ctx_log_typed_surface",
        phone_number="2348000000314",
        channel="whatsapp",
        context_frames=[_query_surface_frame(summary_text="Typed surface")],
    )

    snapshot, source = await _load_query_session_snapshot(planner_state_view(state))

    assert source == "context_frame"
    assert snapshot is not None
    assert (
        "planner_query_session_snapshot",
        {
            "query_session_source": "context_frame",
            "session_active": True,
            "has_query_request": True,
            "has_query_result": True,
            "has_surface": True,
            "has_query_frames": True,
        },
    ) in events


def test_turn_context_summary_builds_compact_shared_view() -> None:
    state = OrchestratorState(
        user_id="u_ctx_1",
        phone_number="2348000000302",
        channel="telegram",
        active_domain="account",
        loaded_context={
            "profile": {"first_name": "Gaines", "last_name": "Doe"},
            "accounts": [
                {
                    "bank_name": "Zenith Bank",
                    "account_number": "00009384",
                    "mandate_status": "ready",
                    "is_default": True,
                },
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
        query_session_source="context_frame",
    )

    assert summary.profile_name == "Gaines Doe"
    assert summary.active_domain == "account"
    assert summary.session_domain == "account"
    assert summary.recent_answer_focus == "linked_accounts_summary"
    assert summary.query_session_active is True
    assert summary.query_session_source == "context_frame"
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


def test_history_prompt_preview_is_limited_to_three_recent_turns() -> None:
    lines = _build_history_lines(
        [
            {"role": "user", "content": "turn one"},
            {"role": "assistant", "content": "turn two"},
            {"role": "user", "content": "turn three"},
            {"role": "assistant", "content": "turn four"},
            {"role": "user", "content": "turn five"},
        ]
    )

    assert lines == ["user: turn three", "agent: turn four", "user: turn five"]


def test_router_context_includes_pending_query_clarification_hint() -> None:
    summary = build_turn_context_summary(
        OrchestratorState(
            user_id="u_ctx_router_pending_1",
            phone_number="2348000000314",
            channel="whatsapp",
            active_domain="query",
        ),
        query_session_snapshot={
            "session_active": True,
            "query_result": {"summary_text": "You spent ₦5,000 today."},
            "pending_clarification": {
                "original_query": "How much did I spend last",
                "resolver_message": "What time period did you mean by last?",
            },
        },
        query_session_source="stashed_compat",
    )

    router_context = build_router_context_from_summary(summary, expected_executors=["transfer"])

    assert "QUERY_SESSION:" in router_context
    assert 'Unresolved query: "How much did I spend last".' in router_context
    assert 'Waiting for: "What time period did you mean by last?".' in router_context


def test_router_context_ignores_inactive_query_session_for_continuation() -> None:
    summary = build_turn_context_summary(
        OrchestratorState(
            user_id="u_ctx_router_1",
            phone_number="2348000000307",
            channel="whatsapp",
            active_domain="query",
        ),
        query_session_snapshot={"session_active": False, "query_result": {"summary_text": "You spent ₦5,000 today."}},
        query_session_source="context_frame",
    )

    router_context = build_router_context_from_summary(summary, expected_executors=["transfer"])

    assert "QUERY_SESSION:" not in router_context


def test_interrupt_and_quoted_context_render_from_shared_summary() -> None:
    state = OrchestratorState(
        user_id="u_ctx_3",
        phone_number="2348000000304",
        channel="whatsapp",
        loaded_context={
            "accounts": [{"bank_name": "First Bank", "account_number": "0334557890", "mandate_status": "pending"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "8162511023"}],
            "history": [
                {"role": "user", "content": "Show my linked accounts"},
                {"role": "assistant", "content": "First Bank is linked but pending."},
            ],
        },
    )
    summary = build_turn_context_summary(state)

    interrupt_context = build_interrupt_context_from_summary(
        summary,
        kind="input",
        task_ids=["t1"],
        current_task_types={"transfer"},
        active_task_state_json='{"t1":{"recipient_name":"Tolu"}}',
        required_fields_json='{"t1":["beneficiary_id"]}',
        prompt_text="Who do you want to send to?",
    )
    quoted_context = build_quoted_replay_context_from_summary(
        summary,
        quoted_message_id="wamid.quoted.1",
        has_quote=True,
        quoted_payload_preview='{"task_type":"transfer","amount":5000}',
    )

    assert len(interrupt_context) <= INTERRUPT_CONTEXT_MAX_CHARS
    assert "Active Flow: input required for tasks ['t1']" in interrupt_context
    assert "RECENT_ANSWER_FOCUS=" in interrupt_context
    assert "ACCOUNTS:" not in interrupt_context
    assert "BENEFICIARIES:" not in interrupt_context

    assert len(quoted_context) <= QUOTED_REPLAY_CONTEXT_MAX_CHARS
    assert "QUOTED_MESSAGE_ID=wamid.quoted.1" in quoted_context
    assert "QUOTED_ACTIONABLE_PAYLOAD=" in quoted_context
    assert "ACCOUNTS:" in quoted_context
    assert "BENEFICIARIES:" in quoted_context


def test_interrupt_compact_context_trims_shared_sections() -> None:
    state = OrchestratorState(
        user_id="u_ctx_3_compact",
        phone_number="2348000000399",
        channel="whatsapp",
        loaded_context={
            "history": [
                {"role": "user", "content": "Send 10k to Mum"},
                {"role": "assistant", "content": "Confirm transfer"},
            ],
        },
    )
    summary = build_turn_context_summary(state)

    compact_context = build_interrupt_context_from_summary(
        summary,
        kind="confirmation",
        task_ids=["t_mum", "t_tolu"],
        current_task_types={"transfer"},
        active_task_state_json='{"t_mum":{"type":"transfer","stage":"awaiting_confirmation"}}',
        required_fields_json="{}",
        prompt_text="Confirm transfers",
        prompt_mode="compact",
    )
    full_context = build_interrupt_context_from_summary(
        summary,
        kind="confirmation",
        task_ids=["t_mum", "t_tolu"],
        current_task_types={"transfer"},
        active_task_state_json='{"t_mum":{"type":"transfer","stage":"awaiting_confirmation"}}',
        required_fields_json="{}",
        prompt_text="Confirm transfers",
        prompt_mode="full",
    )

    assert len(compact_context) <= len(full_context)
    assert "TURN_CONTEXT_ACTIVE_FLOW:" not in compact_context
    assert "RECENT_ANSWER_FOCUS=" in compact_context


def test_get_or_build_turn_context_summary_reuses_cached_state_payload(monkeypatch) -> None:
    state = OrchestratorState(
        user_id="u_ctx_4",
        phone_number="2348000000305",
        channel="whatsapp",
        turn_context_summary=summary_to_state_payload(
            build_turn_context_summary(
                OrchestratorState(
                    user_id="u_ctx_seed",
                    phone_number="2348000000305",
                    channel="whatsapp",
                    loaded_context={"history": [{"role": "assistant", "content": "cached"}]},
                )
            )
        ),
        loaded_context={"history": [{"role": "assistant", "content": "fresh"}]},
    )

    def _should_not_build(*args, **kwargs):
        raise AssertionError("summary should be reused from state")

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary.build_turn_context_summary",
        _should_not_build,
    )

    summary, updates = get_or_build_turn_context_summary(state, path_label="planner_path")

    assert updates is None
    assert summary.history_lines == ["agent: cached"]


def test_turn_context_summary_compacts_pydantic_payload_objects() -> None:
    pending = PendingClarificationState(
        original_query="How much did I spend last",
        current_intent=QueryIntent.ANALYTICS_SUMMARY,
        original_extraction=QueryExtractionResult(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
            raw_query="How much did I spend last",
        ),
        ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
        resolver_message="What time period did you mean by last?",
        language="en",
    )

    state = OrchestratorState(
        user_id="u_ctx_serialize_1",
        phone_number="2348000000315",
        channel="whatsapp",
        session_stack=[ActiveSession(domain="query", state="RUNNING", interrupt_policy="ALLOW")],
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["q1"], fields_by_task={"q1": ["time_range"]}),
        tasks={
            "q1": TaskSpec(
                id="q1",
                type="query",
                stage=TaskStage.EXTRACTED,
                payload={"pending_clarification": pending},
            )
        },
        waves=[["q1"]],
        current_wave_index=0,
    )

    summary = build_turn_context_summary(state)

    assert summary.active_flow_summary is not None
    assert "pending_clarification" in summary.active_flow_summary
    assert "How much did I spend last" in summary.active_flow_summary
