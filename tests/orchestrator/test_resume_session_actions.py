"""Orchestrator resume-session action tests."""

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referents.models import ReferentMemoryItem
from apps.chat.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.task_handlers.runtime import ExecutionAggregation, ExecutionContext
from apps.chat.src.agent.orchestrator.task_handlers.session import handle_orchestrator_task
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave


def _resume_frame() -> ContextFrame:
    now_ts = int(time.time())
    return ContextFrame(
        frame_id="resume-frame",
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_id="resumption_prompt",
                entity_type=EntityType.GENERIC,
                label="Resume transfer",
                data={"intent": "transfer", "resume_prompt": True},
            )
        ],
        created_at_ts=now_ts,
        ttl_seconds=600,
    )


def _keep_frame() -> ContextFrame:
    now_ts = int(time.time())
    return ContextFrame(
        frame_id="keep-frame",
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_id="general-note",
                entity_type=EntityType.GENERIC,
                label="General context",
                data={"resume_prompt": False},
            )
        ],
        created_at_ts=now_ts,
        ttl_seconds=600,
    )


def _ctx(state: OrchestratorState) -> ExecutionContext:
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    return ExecutionContext(
        state=state,
        config=config,
        services={},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )


def _apply_updates(state: OrchestratorState, updates: dict[str, Any]) -> OrchestratorState:
    return state.model_copy(update=updates, deep=True)


class _TransferNeedsConfirmationWorker:
    async def run(
        self,
        *,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={},
            confirmation_summary="Confirm Transaction\n*₦5,000 → Fatima Zahra Musa*\nAccess Bank • 8067892221",
            confirmation_snapshot={
                "amount": 5000.0,
                "recipientName": "Fatima Zahra Musa",
                "sourceBank": "First Bank",
                "sourceAccount": "01234567890",
            },
        )


class _TransferNeedsAuthWorker:
    async def run(
        self,
        *,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_AUTH,
            patch={},
        )


class _TransferNeedsInputWorker:
    async def run(
        self,
        *,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            patch={},
            required_fields=["recipient_account"],
            prompt="What's the account number for Tolu?",
        )


async def test_resume_session_restores_stash_without_replaying_outbox() -> None:
    pending_interrupt = PendingInterrupt(kind="input", task_ids=["t_stashed"], fields_by_task={"t_stashed": ["amount"]})
    stashed_task = TaskSpec(id="t_stashed", type="transfer", stage=TaskStage.EXTRACTED, payload={"amount": 5000})
    orchestrator_task = TaskSpec(
        id="o1",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "resume_session"},
    )

    state = OrchestratorState(
        user_id="u_resume_action_1",
        phone_number="2348000000021",
        channel="whatsapp",
        last_message_text="Yes",
        tasks={"o1": orchestrator_task},
        stashed_sessions=[
            {
                "tasks": {"t_stashed": stashed_task},
                "waves": [["t_stashed"]],
                "current_wave_index": 0,
                "pending_interrupt": pending_interrupt,
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame(), _keep_frame()],
    )

    ctx = _ctx(state)
    await handle_orchestrator_task(orchestrator_task, "o1", ctx)

    assert orchestrator_task.stage == TaskStage.COMPLETED
    assert list(ctx.agg.updates["tasks"].keys()) == ["t_stashed"]
    assert ctx.agg.updates["waves"] == [["t_stashed"]]
    assert ctx.agg.updates["pending_interrupt"] is None
    assert ctx.agg.updates["last_interrupt"] == pending_interrupt
    assert ctx.agg.updates["last_message_text"] is None
    assert ctx.agg.updates["stashed_sessions"] == []
    assert len(ctx.agg.updates["context_frames"]) == 1
    assert ctx.agg.updates["context_frames"][0].frame_id == "keep-frame"
    assert "outbox" not in ctx.agg.updates


async def test_resume_session_clears_matching_stashed_referents() -> None:
    pending_interrupt = PendingInterrupt(kind="input", task_ids=["t_stashed"], fields_by_task={"t_stashed": ["amount"]})
    stashed_task = TaskSpec(id="t_stashed", type="transfer", stage=TaskStage.EXTRACTED, payload={"amount": 5000})
    orchestrator_task = TaskSpec(
        id="o1",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_clear",
        phone_number="2348000000021",
        channel="whatsapp",
        tasks={"o1": orchestrator_task},
        stashed_sessions=[
            {
                "stash_id": "stash-clear",
                "tasks": {"t_stashed": stashed_task},
                "waves": [["t_stashed"]],
                "current_wave_index": 0,
                "pending_interrupt": pending_interrupt,
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame()],
    )
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="stashed_session",
            label="Grace",
            data={"recipient_name": "Grace", "stash_id": "stash-clear"},
        ),
        ReferentMemoryItem(
            referent_type="recipient",
            source="completed_task",
            label="Emeka",
            data={"recipient_name": "Emeka"},
        ),
    ]

    ctx = _ctx(state)
    await handle_orchestrator_task(orchestrator_task, "o1", ctx)

    assert [item.label for item in ctx.agg.updates["referent_memory"].items] == ["Emeka"]


async def test_dismiss_resume_session_clears_matching_stashed_referents() -> None:
    orchestrator_task = TaskSpec(
        id="o1",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "dismiss_resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_dismiss_clear",
        phone_number="2348000000021",
        channel="whatsapp",
        tasks={"o1": orchestrator_task},
        stashed_sessions=[
            {
                "stash_id": "stash-dismiss",
                "tasks": {
                    "t_stashed": TaskSpec(
                        id="t_stashed",
                        type="transfer",
                        stage=TaskStage.EXTRACTED,
                        payload={"amount": 5000},
                    )
                },
                "waves": [["t_stashed"]],
                "current_wave_index": 0,
                "pending_interrupt": {"kind": "input", "task_ids": ["t_stashed"]},
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame()],
    )
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="stashed_session",
            label="Grace",
            data={"recipient_name": "Grace", "stash_id": "stash-dismiss"},
        )
    ]

    ctx = _ctx(state)
    await handle_orchestrator_task(orchestrator_task, "o1", ctx)

    assert orchestrator_task.stage == TaskStage.COMPLETED
    assert ctx.agg.updates["stashed_sessions"] == []
    assert ctx.agg.updates["referent_memory"].items == []


async def test_resume_session_reruns_worker_and_regenerates_confirmation_prompt() -> None:
    pending_interrupt = PendingInterrupt(kind="confirmation", task_ids=["t_stashed"])
    stashed_task = TaskSpec(
        id="t_stashed",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 5000.0,
            "source_account_id": "acc-1",
            "source_account_number": "01234567890",
            "source_bank_name": "First Bank",
            "idempotency_key": "idem-resume-confirm",
            "confirmation": {
                "summary": "Confirm Transaction\n*₦5,000 → Fatima Zahra Musa*\nAccess Bank • 8067892221",
                "snapshot": {
                    "amount": 5000.0,
                    "recipientName": "Fatima Zahra Musa",
                    "sourceBank": "First Bank",
                    "sourceAccount": "01234567890",
                },
            },
        },
    )
    orchestrator_task = TaskSpec(
        id="o2",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_2",
        phone_number="2348000000022",
        channel="whatsapp",
        last_message_text="Yes",
        tasks={"o2": orchestrator_task},
        waves=[["o2"]],
        current_wave_index=0,
        loaded_context={
            "accounts": [
                {
                    "id": "acc-1",
                    "bank_name": "First Bank",
                    "account_number": "01234567890",
                    "available_balance": 30000,
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ]
        },
        stashed_sessions=[
            {
                "tasks": {"t_stashed": stashed_task},
                "waves": [["t_stashed"]],
                "current_wave_index": 0,
                "pending_interrupt": pending_interrupt,
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame()],
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsConfirmationWorker()}},
        "recursion_limit": 50,
    }

    first_updates = await advance_wave(state, config)
    assert first_updates["pending_interrupt"] is None
    assert first_updates["last_interrupt"] == pending_interrupt
    assert first_updates["last_message_text"] is None
    assert "outbox" not in first_updates

    resumed_state = _apply_updates(state, first_updates)
    second_updates = await advance_wave(resumed_state, config)
    assert second_updates["pending_interrupt"].kind == "confirmation"
    assert second_updates["outbox"][0]["type"] == "request_confirmation"
    assert second_updates["outbox"][0]["actionable_payload"]["idempotency_key"] == "idem-resume-confirm"
    assert second_updates["outbox"][0]["actionable_payload"]["task_id"] == "t_stashed"
    assert "From: First Bank (···7890)" in second_updates["outbox"][0]["summary"]
    assert "Bal: ₦30,000.00" in second_updates["outbox"][0]["summary"]
    assert second_updates["pending_interrupt"].prompt == second_updates["outbox"][0]["summary"]


async def test_resume_session_reruns_worker_and_regenerates_auth_prompt() -> None:
    pending_interrupt = PendingInterrupt(kind="auth", task_ids=["t_stashed"], auth_method="pin")
    stashed_task = TaskSpec(
        id="t_stashed",
        type="transfer",
        stage=TaskStage.AWAITING_AUTH,
        payload={
            "idempotency_key": "idem-resume-auth",
            "confirmation": {"summary": "Confirm Transaction\n*₦5,000 → Fatima Zahra Musa*"},
        },
    )
    orchestrator_task = TaskSpec(
        id="o3",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_3",
        phone_number="2348000000023",
        channel="whatsapp",
        last_message_text="Yes",
        tasks={"o3": orchestrator_task},
        waves=[["o3"]],
        current_wave_index=0,
        loaded_context={
            "accounts": [
                {
                    "id": "acc-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ]
        },
        stashed_sessions=[
            {
                "tasks": {"t_stashed": stashed_task},
                "waves": [["t_stashed"]],
                "current_wave_index": 0,
                "pending_interrupt": pending_interrupt,
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame()],
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsAuthWorker()}},
        "recursion_limit": 50,
    }

    resumed_state = _apply_updates(state, await advance_wave(state, config))
    second_updates = await advance_wave(resumed_state, config)

    assert second_updates["pending_interrupt"].kind == "auth"
    assert second_updates["outbox"][0]["type"] == "auth_request"
    assert second_updates["outbox"][0]["idempotency_key"] == "idem-resume-auth"
    assert second_updates["outbox"][0]["actionable_payload"]["idempotency_key"] == "idem-resume-auth"
    assert second_updates["outbox"][0]["actionable_payload"]["task_id"] == "t_stashed"


async def test_resume_session_reruns_worker_and_regenerates_input_prompt() -> None:
    pending_interrupt = PendingInterrupt(kind="input", task_ids=["t_stashed"], fields_by_task={"t_stashed": ["amount"]})
    stashed_task = TaskSpec(
        id="t_stashed",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={"recipient_name": "Tolu"},
    )
    orchestrator_task = TaskSpec(
        id="o4",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_4",
        phone_number="2348000000024",
        channel="whatsapp",
        last_message_text="Yes",
        tasks={"o4": orchestrator_task},
        waves=[["o4"]],
        current_wave_index=0,
        loaded_context={
            "accounts": [
                {
                    "id": "acc-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ]
        },
        stashed_sessions=[
            {
                "tasks": {"t_stashed": stashed_task},
                "waves": [["t_stashed"]],
                "current_wave_index": 0,
                "pending_interrupt": pending_interrupt,
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame()],
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsInputWorker()}},
        "recursion_limit": 50,
    }

    resumed_state = _apply_updates(state, await advance_wave(state, config))
    second_updates = await advance_wave(resumed_state, config)

    assert second_updates["pending_interrupt"].kind == "input"
    assert second_updates["outbox"][0]["type"] == "say"
    assert second_updates["outbox"][0]["text"] == "What's the account number for Tolu?"


async def test_confirmation_source_line_without_cached_balance_uses_default_template() -> None:
    state = OrchestratorState(
        user_id="u_resume_action_5",
        phone_number="2348000000025",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.DRAFT,
                payload={
                    "amount": 5000.0,
                    "source_account_id": "acc-1",
                    "source_account_number": "01234567890",
                    "source_bank_name": "First Bank",
                    "idempotency_key": "idem-confirm-default-source-line",
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "accounts": [
                {
                    "id": "acc-1",
                    "bank_name": "First Bank",
                    "account_number": "01234567890",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsConfirmationWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    request_confirmation = updates["outbox"][0]
    assert request_confirmation["type"] == "request_confirmation"
    assert request_confirmation["header"] == "Confirm Transfer"
    assert request_confirmation["actionable_payload"]["idempotency_key"] == "idem-confirm-default-source-line"
    assert request_confirmation["actionable_payload"]["source_bank_name"] == "First Bank"
    assert "From: First Bank (···7890)" in request_confirmation["summary"]
    assert "Bal:" not in request_confirmation["summary"]


async def test_dismiss_resume_session_clears_stash_and_acknowledges() -> None:
    orchestrator_task = TaskSpec(
        id="o6",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "dismiss_resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_6",
        phone_number="2348000000026",
        channel="whatsapp",
        tasks={"o6": orchestrator_task},
        stashed_sessions=[
            {
                "tasks": {},
                "waves": [],
                "current_wave_index": 0,
                "pending_interrupt": None,
                "intent": "transfer",
            }
        ],
        context_frames=[_resume_frame(), _keep_frame()],
    )

    ctx = _ctx(state)
    await handle_orchestrator_task(orchestrator_task, "o6", ctx)

    assert orchestrator_task.stage == TaskStage.COMPLETED
    assert ctx.agg.updates["stashed_sessions"] == []
    assert len(ctx.agg.updates["context_frames"]) == 1
    assert ctx.agg.updates["context_frames"][0].frame_id == "keep-frame"
    assert ctx.agg.updates["outbox"][0]["text"] == "Okay, I won't resume that request."


async def test_dismiss_resume_session_without_stash_is_safe() -> None:
    orchestrator_task = TaskSpec(
        id="o7",
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": "dismiss_resume_session"},
    )
    state = OrchestratorState(
        user_id="u_resume_action_7",
        phone_number="2348000000027",
        channel="whatsapp",
        tasks={"o7": orchestrator_task},
        stashed_sessions=[],
        context_frames=[_resume_frame()],
    )

    ctx = _ctx(state)
    await handle_orchestrator_task(orchestrator_task, "o7", ctx)

    assert orchestrator_task.stage == TaskStage.FAILED
    assert orchestrator_task.payload["error"] == "No session to resume."
    assert ctx.agg.updates["outbox"][0]["text"] == "No session to resume."


def test_context_manager_summary_includes_resume_prompt_context() -> None:
    state = OrchestratorState(
        user_id="u_resume_action_8",
        phone_number="2348000000028",
        channel="whatsapp",
        context_frames=[_resume_frame()],
    )

    summary = OrchestratorContextManager().build_llm_summary(state)

    assert "Asked to resume transfer" in summary
