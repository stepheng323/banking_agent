import time
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage, TransactionOutcome, TransactionResult
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.execution import advance_wave
from apps.chat.src.agent.orchestrator.nodes.finalize import finalize
from apps.chat.src.agent.orchestrator.nodes.gate.runner import session_gate_direct_path


def _apply_updates(state: OrchestratorState, updates: dict[str, Any]) -> OrchestratorState:
    return state.model_copy(update=updates)


def _config(data_worker: Any | None = None) -> RunnableConfig:
    services = {"data": data_worker} if data_worker is not None else {}
    return {"configurable": {"services": services, "redis_client": None}, "recursion_limit": 50}


class _DataPlanQueryWorker:
    def __init__(self, result: TransactionResult) -> None:
        self.result = result
        self.call_count = 0
        self.last_payload: dict[str, Any] | None = None

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        self.call_count += 1
        self.last_payload = dict(payload)
        return self.result


class _PinSensitiveDataWorker:
    def __init__(self) -> None:
        self.last_pin_verified: bool | None = None

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message
        self.last_pin_verified = pin_verified
        if pin_verified:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt={
                    "status": "processing",
                    "message": "Got it. Your data purchase is processing.",
                },
                patch={"receipt": {"status": "processing"}},
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Data ready for your number: MTN 5 GB data bundle",
            confirmation_snapshot=dict(payload),
            patch=dict(payload),
        )


@pytest.mark.asyncio
async def test_data_plan_query_gate_marks_task_read_only_for_finalize() -> None:
    state = OrchestratorState(
        user_id="u_data_query_gate",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="How much is 5GB MTN?",
    )

    updates = await session_gate_direct_path(state, _config())

    task = next(iter(updates["tasks"].values()))
    assert task.type == "data"
    assert task.payload["action"] == "data_plan_query"
    assert task.payload["skip_finalize_summary"] is True


@pytest.mark.asyncio
async def test_buy_it_with_stale_pin_stops_at_confirmation_not_processing() -> None:
    worker = _PinSensitiveDataWorker()
    state = OrchestratorState(
        user_id="u_data_buy_stale_pin",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="Buy it",
        pin_verified=True,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acc_1",
                    "bank_name": "Access Bank",
                    "account_name": "Gaines",
                    "account_number": "2010000003",
                    "is_default": True,
                    "mandate_status": "ready",
                }
            ],
        },
        context_frames=[
            ContextFrame(
                frame_id="data_plan_frame",
                frame_type=ContextFrameType.DATA_PLAN_LIST,
                created_at_ts=int(time.time()),
                items=[
                    ContextEntity(
                        entity_type=EntityType.DATA_PLAN,
                        entity_id="MD501",
                        label="MTN 5 GB data bundle",
                        data={
                            "index": 1,
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                            "network": "MTN",
                            "amount": 3500,
                            "validity_days": 30,
                        },
                    )
                ],
            )
        ],
    )

    gate_updates = await session_gate_direct_path(state, _config(worker))
    gated_state = _apply_updates(state, gate_updates)
    execution_updates = await advance_wave(gated_state, _config(worker))

    assert worker.last_pin_verified is False
    assert execution_updates["pending_interrupt"].kind == "confirmation"
    assert execution_updates["outbox"][0]["type"] == "request_confirmation"
    assert "receipt" not in next(iter(execution_updates["tasks"].values())).payload


@pytest.mark.asyncio
async def test_data_plan_query_execution_emits_single_catalog_response_without_purchase_summary() -> None:
    response = "MTN 3.5 GB on MTN costs ₦2,000 and lasts 30 days."
    worker = _DataPlanQueryWorker(
        TransactionResult(
            outcome=TransactionOutcome.OK,
            response=response,
            patch={
                "data_plan_query_results": [
                    {
                        "plan_code": "MD108",
                        "plan_name": "MTN 3.5 GB",
                        "network": "MTN",
                        "amount": 2000,
                        "validity_days": 30,
                    }
                ]
            },
        )
    )
    state = OrchestratorState(
        user_id="u_data_query_execution",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="How much is 3.5GB MTN?",
        loaded_context={"language": "en", "accounts": []},
        tasks={
            "data_query_1": TaskSpec(
                id="data_query_1",
                type="data",
                stage=TaskStage.DRAFT,
                payload={"action": "data_plan_query", "network": "MTN", "size_preference": "3.5GB"},
            )
        },
        waves=[["data_query_1"]],
    )

    execution_updates = await advance_wave(state, _config(worker))

    assert worker.call_count == 1
    assert execution_updates["outbox"] == [{"type": "say", "text": response}]
    frame = execution_updates["context_frames"][-1]
    assert frame.frame_type == ContextFrameType.DATA_PLAN_LIST
    assert frame.items[0].entity_type == EntityType.DATA_PLAN
    assert frame.items[0].data["plan_code"] == "MD108"
    data_plan_referent = next(
        item for item in execution_updates["referent_memory"].items if item.referent_type == "data_plan"
    )
    assert data_plan_referent.data["plan_code"] == "MD108"
    task = execution_updates["tasks"]["data_query_1"]
    assert task.stage == TaskStage.COMPLETED
    assert task.payload["skip_finalize_summary"] is True

    final_updates = await finalize(_apply_updates(state, execution_updates), _config())
    assert final_updates["outbox"] == [{"type": "say", "text": response}]


@pytest.mark.asyncio
async def test_data_plan_purchase_candidates_seed_context_frame_and_memory() -> None:
    worker = _DataPlanQueryWorker(
        TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["data_plan_id"],
            prompt="Which MTN data plan should I use?",
            details={
                "options": [
                    {"label": "MTN 5 GB data bundle", "value": "MD501"},
                    {"label": "MTN 3.5 GB", "value": "MD108"},
                ]
            },
            patch={
                "data_plan_candidates": [
                    {
                        "index": 1,
                        "plan_code": "MD501",
                        "plan_name": "MTN 5 GB data bundle",
                        "network": "MTN",
                        "amount": 3500,
                        "validity_days": 30,
                    },
                    {
                        "index": 2,
                        "plan_code": "MD108",
                        "plan_name": "MTN 3.5 GB",
                        "network": "MTN",
                        "amount": 2000,
                        "validity_days": 30,
                    },
                ]
            },
        )
    )
    state = OrchestratorState(
        user_id="u_data_purchase_candidates",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="Buy MTN data",
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                }
            ],
        },
        tasks={
            "data_1": TaskSpec(
                id="data_1",
                type="data",
                stage=TaskStage.DRAFT,
                payload={"action": "buy_data", "network": "MTN"},
            )
        },
        waves=[["data_1"]],
    )

    execution_updates = await advance_wave(state, _config(worker))

    assert execution_updates["pending_interrupt"].kind == "input"
    frame = execution_updates["context_frames"][-1]
    assert frame.frame_type == ContextFrameType.DATA_PLAN_LIST
    assert [item.data["plan_code"] for item in frame.items] == ["MD501", "MD108"]
    memory_codes = [
        item.data.get("plan_code")
        for item in execution_updates["referent_memory"].items
        if item.referent_type == "data_plan"
    ]
    assert memory_codes == ["MD501", "MD108"]


@pytest.mark.asyncio
async def test_failed_data_plan_query_response_is_not_duplicated_by_finalize() -> None:
    response = "data plan query isn't available yet. I can help with supported banking tasks only."
    worker = _DataPlanQueryWorker(
        TransactionResult(
            outcome=TransactionOutcome.FAILED,
            response=response,
            error=response,
            patch={"capability_blocked": True},
        )
    )
    state = OrchestratorState(
        user_id="u_data_query_failure",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="How much is 3.5GB MTN?",
        loaded_context={"language": "en", "accounts": []},
        tasks={
            "data_query_1": TaskSpec(
                id="data_query_1",
                type="data",
                stage=TaskStage.DRAFT,
                payload={"action": "data_plan_query", "network": "MTN", "size_preference": "3.5GB"},
            )
        },
        waves=[["data_query_1"]],
    )

    execution_updates = await advance_wave(state, _config(worker))

    assert "outbox" not in execution_updates
    final_updates = await finalize(_apply_updates(state, execution_updates), _config())
    assert final_updates["outbox"] == [{"type": "say", "text": response}]
