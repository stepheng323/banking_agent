"""Latency guard tests for planner/context prompt growth."""

import time
from typing import Any

import pytest
import tiktoken
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT,
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _build_query_session_context,
    _build_user_state_summary,
    plan_tasks,
)
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.services.task_planner import (
    PlannerPromptBuildInput,
    PlannerPromptSignals,
    build_planner_system_prompt,
)
from shared.types.planner import PlannerOutput

_PROMPT_SIZE_BASELINE = {
    "generic": 8475,
    "transfer_only": 9457,
    "mixed_money_move": 9457,
    "fallback_money_move": 9479,
    "query": 9141,
    "context_followup": 9479,
    "fully_expanded": 10859,
}


def _runtime_prompt_size_report() -> dict[str, int]:
    profiles = {
        "generic": ("hello", "None", PlannerPromptSignals()),
        "transfer_only": (
            "okay send 10k each to mum, tolu and doyin",
            "Recent user state summary",
            PlannerPromptSignals(
                forced_domain_owner="transfer",
                expected_transaction_executors=("transfer",),
            ),
        ),
        "mixed_money_move": (
            "Send 10k to mum and buy me 5k airtime",
            "None",
            PlannerPromptSignals(expected_transaction_executors=("transfer", "airtime")),
        ),
        "fallback_money_move": (
            "make it 20k tomorrow 9am",
            "Active transfer flow",
            PlannerPromptSignals(active_flow_type="transfer"),
        ),
        "query": (
            "How much did I spend last week?",
            "None",
            PlannerPromptSignals(query_session_active=True, recent_domain_focus="query"),
        ),
        "context_followup": (
            "List them",
            "Recent Chat last turn was account_count answer",
            PlannerPromptSignals(active_flow_type="account", has_beneficiary_suggestion=True),
        ),
        "fully_expanded": (
            "Send 10k to Mum and buy me 5k airtime and show transactions",
            "Active Query Session. Asked to save beneficiary. Recent Chat.",
            PlannerPromptSignals(
                active_flow_type="transfer",
                query_session_active=True,
                recent_domain_focus="query",
                has_beneficiary_suggestion=True,
                expected_transaction_executors=("transfer", "airtime"),
            ),
        ),
    }
    report: dict[str, int] = {}
    for name, (text, context, signals) in profiles.items():
        result = build_planner_system_prompt(
            PlannerPromptBuildInput(text=text, context=context, signals=signals)
        )
        report[name] = len(result.system_prompt)
    return report


def _runtime_prompt_token_report() -> dict[str, int]:
    encoding = tiktoken.get_encoding("o200k_base")
    profiles = {
        "generic": ("hello", "None", PlannerPromptSignals()),
        "transfer_only": (
            "okay send 10k each to mum, tolu and doyin",
            "Recent user state summary",
            PlannerPromptSignals(
                forced_domain_owner="transfer",
                expected_transaction_executors=("transfer",),
            ),
        ),
        "mixed_money_move": (
            "Send 10k to mum and buy me 5k airtime",
            "None",
            PlannerPromptSignals(expected_transaction_executors=("transfer", "airtime")),
        ),
        "fallback_money_move": (
            "make it 20k tomorrow 9am",
            "Active transfer flow",
            PlannerPromptSignals(active_flow_type="transfer"),
        ),
        "query": (
            "How much did I spend last week?",
            "None",
            PlannerPromptSignals(query_session_active=True, recent_domain_focus="query"),
        ),
        "context_followup": (
            "List them",
            "Recent Chat last turn was account_count answer",
            PlannerPromptSignals(active_flow_type="account", has_beneficiary_suggestion=True),
        ),
        "fully_expanded": (
            "Send 10k to Mum and buy me 5k airtime and show transactions",
            "Active Query Session. Asked to save beneficiary. Recent Chat.",
            PlannerPromptSignals(
                active_flow_type="transfer",
                query_session_active=True,
                recent_domain_focus="query",
                has_beneficiary_suggestion=True,
                expected_transaction_executors=("transfer", "airtime"),
            ),
        ),
    }
    report: dict[str, int] = {}
    for name, (text, context, signals) in profiles.items():
        result = build_planner_system_prompt(
            PlannerPromptBuildInput(text=text, context=context, signals=signals)
        )
        report[name] = len(encoding.encode(result.system_prompt))
    return report


class _CapturingPlanner:
    planner_llm: Any | None

    def __init__(self, output: PlannerOutput) -> None:
        self._output = output
        self.planner_llm = object()
        self.last_context: str | None = None
        self.last_prompt_signals: object | None = None

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text
        self.last_context = context
        self.last_prompt_signals = prompt_signals
        return self._output


@pytest.mark.asyncio
async def test_plan_tasks_caps_large_context_before_planner_call() -> None:
    planner = _CapturingPlanner(
        PlannerOutput(
            primary_intent="conversational",
            response="Noted.",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype=None,
            normalized_instruction="continue",
            tasks=[],
        )
    )

    large_note = "x" * 9000
    state = OrchestratorState(
        user_id="u_cap_1",
        phone_number="2348011111111",
        channel="whatsapp",
        last_message_text="continue",
        loaded_context={
            "history": [
                {"role": "user", "content": large_note},
                {"role": "assistant", "content": large_note},
                {"role": "user", "content": large_note},
                {"role": "assistant", "content": large_note},
                {"role": "user", "content": large_note},
            ]
        },
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["recipient"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "action": "send_money",
                    "recipient_name": "Tolu",
                    "metadata": {"long_note": large_note, "hints": [large_note] * 8},
                    "events": [{"ts": i, "text": large_note} for i in range(8)],
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    await plan_tasks(state, config)

    assert planner.last_context is not None
    assert len(planner.last_context) <= PLANNER_CONTEXT_MAX_CHARS
    assert "Current Task Data:" in planner.last_context
    assert "...[truncated]" in planner.last_context


@pytest.mark.asyncio
async def test_plan_tasks_skips_full_context_for_lightweight_turns() -> None:
    planner = _CapturingPlanner(
        PlannerOutput(
            primary_intent="beneficiary",
            response="",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype=None,
            normalized_instruction="show my beneficiaries",
            tasks=[],
        )
    )
    state = OrchestratorState(
        user_id="u_cap_min_1",
        phone_number="2348044444444",
        channel="whatsapp",
        last_message_text="show my beneficiaries",
        loaded_context={
            "history": [
                {"role": "user", "content": "random history " * 80},
                {"role": "assistant", "content": "random reply " * 80},
            ],
            "accounts": [{"bank_name": "First Bank", "account_number": "0000000001", "mandate_status": "ready"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "0123456789"}],
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    await plan_tasks(state, config)

    assert planner.last_context == "None"
    assert planner.last_prompt_signals is not None
    assert getattr(planner.last_prompt_signals, "has_short_term_memory", False) is False
    assert getattr(planner.last_prompt_signals, "has_user_state_summary", False) is False
    assert getattr(planner.last_prompt_signals, "recent_domain_focus", None) is None


@pytest.mark.asyncio
async def test_plan_tasks_marks_guardrail_transfer_handoff_for_transfer_only_prompt() -> None:
    planner = _CapturingPlanner(
        PlannerOutput(
            primary_intent="transfer",
            response="",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype=None,
            normalized_instruction="send 10k each to mum, tolu and doyin",
            tasks=[],
        )
    )
    state = OrchestratorState(
        user_id="u_transfer_prompt_1",
        phone_number="2348044444455",
        channel="whatsapp",
        last_message_text="okay send 10k each to mum, tolu and doyin",
        routing_owner="guardrail",
        routing_decision="batch_transfer_command",
        routing_target_domain="transfer",
        preplanner_expected_transaction_executors=["transfer"],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    await plan_tasks(state, config)

    assert planner.last_prompt_signals is not None
    assert getattr(planner.last_prompt_signals, "forced_domain_owner", None) == "transfer"
    assert getattr(planner.last_prompt_signals, "expected_transaction_executors", ()) == ("transfer",)


@pytest.mark.asyncio
async def test_plan_tasks_marks_narrow_transfer_interrupt_for_transfer_only_prompt() -> None:
    planner = _CapturingPlanner(
        PlannerOutput(
            primary_intent="transfer",
            response="",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype=None,
            normalized_instruction="8967855634, First bank",
            tasks=[],
        )
    )
    state = OrchestratorState(
        user_id="u_transfer_prompt_2",
        phone_number="2348044444466",
        channel="whatsapp",
        last_message_text="8967855634, First bank",
        routing_owner="guardrail",
        routing_decision="account_aware_transfer_command",
        routing_target_domain="transfer",
        preplanner_expected_transaction_executors=["transfer"],
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["recipient_account"]}),
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    await plan_tasks(state, config)

    assert planner.last_prompt_signals is not None
    assert getattr(planner.last_prompt_signals, "forced_domain_owner", None) == "transfer"


@pytest.mark.asyncio
async def test_plan_tasks_trims_user_state_for_narrow_transfer_replan() -> None:
    planner = _CapturingPlanner(
        PlannerOutput(
            primary_intent="transfer",
            response="",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype=None,
            normalized_instruction="make it 20k",
            tasks=[],
        )
    )
    state = OrchestratorState(
        user_id="u_transfer_prompt_trim_1",
        phone_number="2348044444467",
        channel="whatsapp",
        last_message_text="make it 20k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1"], fields_by_task={"t1": []}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Mum",
                    "confirmation": {"summary": "Confirm transfer"},
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "history": [{"role": "assistant", "content": "Confirm the transfer to Mum."}],
            "accounts": [{"bank_name": "First Bank", "account_number": "0000000001", "mandate_status": "ready"}],
            "beneficiaries": [{"alias": "Mum", "bank_name": "Opay", "account_number": "0123456789"}],
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    await plan_tasks(state, config)

    assert planner.last_context is not None
    assert "Active Flow:" in planner.last_context
    assert "User State:" not in planner.last_context
    assert "Recent Chat:" not in planner.last_context


@pytest.mark.asyncio
async def test_plan_tasks_keeps_context_for_referential_followups() -> None:
    planner = _CapturingPlanner(
        PlannerOutput(
            primary_intent="conversational",
            response="Here are your linked accounts.",
            response_key=None,
            confidence=0.9,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype="linked_accounts_summary",
            normalized_instruction="show them",
            tasks=[],
        )
    )
    state = OrchestratorState(
        user_id="u_cap_min_2",
        phone_number="2348055555555",
        channel="telegram",
        last_message_text="show them",
        loaded_context={
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0001"},
                {"bank_name": "GTBank", "account_number": "0002"},
            ],
            "history": [
                {"role": "user", "content": "How many accounts do I have linked"},
                {"role": "assistant", "content": "You have 2 linked accounts."},
            ],
        },
        planner_output=PlannerOutput(
            primary_intent="conversational",
            response="You have 2 linked accounts.",
            response_key=None,
            confidence=0.93,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype="account_count",
            normalized_instruction="how many accounts do i have linked",
            tasks=[],
        ),
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    await plan_tasks(state, config)

    assert planner.last_context is not None
    assert planner.last_context != "None"
    assert "Recent Domain Focus: account" in planner.last_context


def test_user_state_summary_caps_account_preview() -> None:
    accounts = [
        {
            "bank_name": f"Bank {idx}",
            "account_number": f"000000000{idx}",
            "mandate_status": "ready",
            "is_default": idx == 1,
        }
        for idx in range(1, CONTEXT_ACCOUNT_PREVIEW_LIMIT + 4)
    ]
    state = OrchestratorState(
        user_id="u_cap_2",
        phone_number="2348022222222",
        channel="whatsapp",
        loaded_context={"accounts": accounts},
    )

    summary = _build_user_state_summary(state)
    assert summary is not None
    assert summary.count("mandate:") == CONTEXT_ACCOUNT_PREVIEW_LIMIT
    assert "+3 more account(s)" in summary


def test_context_frame_summary_caps_item_preview() -> None:
    now = int(time.time())
    entities = [
        ContextEntity(
            entity_type=EntityType.BENEFICIARY,
            label=f"Beneficiary {idx}",
            data={"bank": "Bank", "account": f"000{idx}"},
        )
        for idx in range(1, 10)
    ]
    frame = ContextFrame(
        frame_id="frame_cap_1",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=entities,
        created_at_ts=now,
        source_message_id="msg_1",
    )
    state = OrchestratorState(
        user_id="u_cap_3",
        phone_number="2348033333333",
        channel="whatsapp",
        context_frames=[frame],
    )

    summary = OrchestratorContextManager().build_llm_summary(state)
    assert "... (+4 more)" in summary
    assert "Beneficiary 1" in summary


def test_planner_context_assembly_drops_low_priority_sections_when_budget_exceeded() -> None:
    context, included, clipped, dropped = _assemble_planner_context(
        [
            ("high_priority", "A" * 180),
            ("low_priority", "B" * 180),
        ],
        max_chars=240,
    )

    assert len(context) <= 240
    assert included == ["high_priority"]
    assert clipped == []
    assert dropped == ["low_priority"]


def test_query_session_context_builder_keeps_required_query_guidance() -> None:
    context = _build_query_session_context("You spent ₦5,000 today.")

    assert "Active Query Session" in context
    assert "NOT conversational questions" in context
    assert "Always route them as query tasks." in context


def test_runtime_prompt_size_report_and_budget_guard(capsys: pytest.CaptureFixture[str]) -> None:
    report = _runtime_prompt_size_report()
    token_report = _runtime_prompt_token_report()

    with capsys.disabled():
        print("planner_runtime_prompt_sizes:")
        for key in (
            "generic",
            "transfer_only",
            "mixed_money_move",
            "fallback_money_move",
            "query",
            "context_followup",
            "fully_expanded",
        ):
            before = _PROMPT_SIZE_BASELINE[key]
            after = report[key]
            delta = before - after
            pct = (delta / before) * 100
            print(f"- {key}: before={before} after={after} delta={delta} ({pct:.1f}%)")
        print("planner_runtime_prompt_tokens:")
        for key in (
            "generic",
            "transfer_only",
            "mixed_money_move",
            "fallback_money_move",
            "query",
            "context_followup",
            "fully_expanded",
        ):
            print(f"- {key}: tokens={token_report[key]}")

    assert report["generic"] <= 1600
    assert report["transfer_only"] <= 2700
    assert report["mixed_money_move"] <= 3000
    assert report["fallback_money_move"] <= 2800
    assert report["query"] <= 1850
    assert report["context_followup"] <= 2100
    assert report["fully_expanded"] <= 3900

    assert token_report["generic"] <= 390
    assert token_report["transfer_only"] <= 650
    assert token_report["mixed_money_move"] <= 760
    assert token_report["fallback_money_move"] <= 720
    assert token_report["query"] <= 450
    assert token_report["context_followup"] <= 510
    assert token_report["fully_expanded"] <= 980
