from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.support import (
    FAQTaskExecutor,
    SupportTaskExecutor,
)
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.policy.loader import get_cached_policy
from banking.runtime.results import (
    FAQOutcome,
    FAQResult,
    SupportOutcome,
    SupportResult,
)
from banking.support.models import RetryTransferHandoff, RetryTransferPayload
from banking.support.worker import SupportWorker

CAPABILITY_POLICY_PATH = "banking/policy/defaults/capability_policy.json"
SUPPORT_DISABLED_MESSAGE = (
    "Support help is temporarily unavailable. I can still help with transfers, airtime/data purchase, balances, and "
    "transaction queries."
)


class _FAQHandoffWorker:
    async def run(self, payload, context, user_message=None, pin_verified=False):
        del payload, context, user_message, pin_verified
        return FAQResult(
            outcome=FAQOutcome.OK,
            response="Let me connect you with support.",
            should_route_to_support=True,
        )


class _SupportOKWorker:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, payload, context, user_message=None, pin_verified=False):
        self.calls.append(
            {
                "payload": dict(payload),
                "context": dict(context),
                "user_message": user_message,
                "pin_verified": pin_verified,
            }
        )
        return SupportResult(outcome=SupportOutcome.OK, response="Support handled this.")


class _SupportRetryWorker:
    async def run(self, payload, context, user_message=None, pin_verified=False):
        del payload, context, user_message, pin_verified
        return SupportResult(
            outcome=SupportOutcome.OK,
            response="I found the failed transfer. Review it before retrying.",
            handoff=RetryTransferHandoff(
                payload=RetryTransferPayload(
                    amount=5000,
                    recipient_name="Tolu",
                    recipient_account_number="8162511023",
                    recipient_bank_name="OPay",
                    recipient_bank_code="999992",
                    source_bank_name="GTBank",
                )
            ),
        )


def _ctx(*, support_worker) -> tuple[TaskSpec, ExecutionTurnContext]:
    task = TaskSpec(
        id="faq_1",
        type="faq",
        stage=TaskStage.DRAFT,
        payload={"action": "answer_question"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000000",
        channel="whatsapp",
        channel_identity="2348000000000",
        last_message_text="Why did my transfer fail?",
        tasks={task.id: task},
        loaded_context={"language": "en", "user_id": "user-1", "profile": {"email": "u@example.com"}},
    )
    agg = ExecutionAccumulator(state.tasks)
    return task, ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"faq": _FAQHandoffWorker(), "support": support_worker}),
        current_wave_len=1,
        accumulator=agg,
        current_wave_task_ids=[task.id],
    )


@pytest.mark.asyncio
async def test_faq_handoff_runs_support_same_turn_without_duplicate_handoff_text() -> None:
    support_worker = _SupportOKWorker()
    task, ctx = _ctx(support_worker=support_worker)

    await FAQTaskExecutor().execute(task, task.id, ctx)

    assert task.type == "support"
    assert task.stage == TaskStage.COMPLETED
    assert support_worker.calls
    assert support_worker.calls[0]["user_message"] == "Why did my transfer fail?"
    assert support_worker.calls[0]["payload"]["action"] == "handle_request"
    assert ctx.accumulator.to_updates()["outbox"] == [{"type": "say", "text": "Support handled this."}]


@pytest.mark.asyncio
async def test_faq_handoff_respects_disabled_support_policy(tmp_path: Path) -> None:
    raw = get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True).model_dump()
    raw["capability_matrix"]["support"]["enabled"] = False
    raw["capability_matrix"]["support"]["limitation_message"] = SUPPORT_DISABLED_MESSAGE
    policy_path = tmp_path / "capability_policy_support_disabled.json"
    policy_path.write_text(json.dumps(raw, ensure_ascii=True), encoding="utf-8")
    get_cached_policy(path=str(policy_path), force_reload=True)

    try:
        task, ctx = _ctx(
            support_worker=SupportWorker(
                llm=None,
                transaction_repo=None,
                actionable_message_repo=None,
                redis_client=None,
            )
        )

        await FAQTaskExecutor().execute(task, task.id, ctx)

        assert task.type == "support"
        assert task.stage == TaskStage.COMPLETED
        assert ctx.accumulator.to_updates()["outbox"] == [{"type": "say", "text": SUPPORT_DISABLED_MESSAGE}]
    finally:
        get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True)


@pytest.mark.asyncio
async def test_support_retry_handoff_creates_fresh_transfer_draft_without_authorization() -> None:
    task = TaskSpec(
        id="support_1",
        type="support",
        stage=TaskStage.DRAFT,
        payload={"action": "handle_request"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="Retry that failed transfer",
        tasks={task.id: task},
        waves=[[task.id]],
        current_wave_index=0,
        loaded_context={"language": "en", "user_id": "user-1"},
    )
    accumulator = ExecutionAccumulator(state.tasks)
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"support": _SupportRetryWorker()}),
        current_wave_len=1,
        accumulator=accumulator,
    )

    await SupportTaskExecutor().execute(task, task.id, ctx)

    updates = accumulator.to_updates()
    retry_task = updates["tasks"]["query_handoff_transfer_1"]
    assert task.stage == TaskStage.COMPLETED
    assert retry_task.type == "transfer"
    assert retry_task.stage == TaskStage.DRAFT
    assert retry_task.payload["action"] == "send_money"
    assert retry_task.payload["recipient_account"] == "8162511023"
    assert retry_task.payload.get("confirmation", {}).get("confirmed") is not True
    assert "authorization" not in retry_task.payload
    assert updates["waves"] == [["support_1"], ["query_handoff_transfer_1"]]
