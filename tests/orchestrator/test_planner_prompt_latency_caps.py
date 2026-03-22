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
    "mixed_money_move": 9457,
    "query": 9141,
    "context_followup": 9479,
    "fully_expanded": 10859,
}


def _runtime_prompt_size_report() -> dict[str, int]:
    profiles = {
        "generic": ("hello", "None", PlannerPromptSignals()),
        "mixed_money_move": (
            "Send 10k to mum and buy me 5k airtime",
            "None",
            PlannerPromptSignals(expected_transaction_executors=("transfer", "airtime")),
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
        "mixed_money_move": (
            "Send 10k to mum and buy me 5k airtime",
            "None",
            PlannerPromptSignals(expected_transaction_executors=("transfer", "airtime")),
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

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text
        self.last_context = context
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
        for key in ("generic", "mixed_money_move", "query", "context_followup", "fully_expanded"):
            before = _PROMPT_SIZE_BASELINE[key]
            after = report[key]
            delta = before - after
            pct = (delta / before) * 100
            print(f"- {key}: before={before} after={after} delta={delta} ({pct:.1f}%)")
        print("planner_runtime_prompt_tokens:")
        for key in ("generic", "mixed_money_move", "query", "context_followup", "fully_expanded"):
            print(f"- {key}: tokens={token_report[key]}")

    assert report["generic"] <= 1600
    # Money-move one-shot multilingual + recipient-split coverage intentionally increases this bundle.
    assert report["mixed_money_move"] <= 3900
    assert report["query"] <= 1850
    assert report["context_followup"] <= 2100
    assert report["fully_expanded"] <= 4350

    assert token_report["generic"] <= 390
    assert token_report["mixed_money_move"] <= 1100
    assert token_report["query"] <= 450
    assert token_report["context_followup"] <= 510
    assert token_report["fully_expanded"] <= 1130
