"""Latency guard tests for planner/context prompt growth."""

import time
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT,
    PLANNER_CONTEXT_MAX_CHARS,
    _build_user_state_summary,
    plan_tasks,
)
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.types.planner import PlannerOutput


class _CapturingPlanner:
    planner_llm: Any | None

    def __init__(self, output: PlannerOutput) -> None:
        self._output = output
        self.planner_llm = object()
        self.last_context: str | None = None

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
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
            context_fastpath_subtype=None,
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
