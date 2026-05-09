import time

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.finalize import finalize
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import ContextFrameFollowupDecision, PlannedTask, PlannerOutput, TaskParameters


class _SurfaceFollowupPlanner:
    def __init__(
        self,
        decision: ContextFrameFollowupDecision,
        planner_output: PlannerOutput | None = None,
    ) -> None:
        self.decision = decision
        self.planner_output = planner_output
        self.last_frame_context: str | None = None
        self.plan_calls = 0

    async def interpret_context_frame_followup(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameFollowupDecision:
        del phone_number, text, path_label
        self.last_frame_context = context
        return self.decision

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
    ) -> PlannerOutput:
        del phone_number, text, context, prompt_signals
        self.plan_calls += 1
        if self.planner_output is None:
            raise AssertionError("planner should not be called for grounded frame follow-up")
        return self.planner_output


def _config(planner: _SurfaceFollowupPlanner) -> RunnableConfig:
    return {
        "configurable": {
            "task_planner": planner,
            "beneficiary_suggestion_service": None,
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }


def _transaction_list_frame() -> ContextFrame:
    now = int(time.time())
    return ContextFrame(
        frame_id="tx_list_recent",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-1",
                label="Credit from Ada",
                data={
                    "amount": 5000,
                    "bank_name": "GTBank",
                    "transaction_type": "credit",
                    "status": "successful",
                    "date": "2026-05-01",
                },
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-2",
                label="Transfer to Tolu",
                data={
                    "amount": 2000,
                    "bank_name": "Access Bank",
                    "transaction_type": "debit",
                    "status": "successful",
                    "date": "2026-05-02",
                    "reference": "tx-ref-2",
                },
            ),
        ],
        created_at_ts=now,
        ttl_seconds=600,
    )


@pytest.mark.asyncio
async def test_transaction_surface_followup_selects_second_visible_item() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="select_item",
            confidence=0.94,
            selection_index=2,
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_select",
        phone_number="2348000000010",
        channel="telegram",
        last_message_text="show the second one",
        context_frames=[_transaction_list_frame()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transaction Details" in updates["final_response"]
    assert "Transfer to Tolu" in updates["final_response"]
    assert "Access Bank" in updates["final_response"]
    assert "tx-ref-2" in updates["final_response"]
    assert "Credit from Ada" not in updates["final_response"]


@pytest.mark.asyncio
async def test_completed_transfer_receipt_followup_answers_reference_from_frame() -> None:
    finalize_state = OrchestratorState(
        user_id="u_surface_receipt_ref",
        phone_number="2348000000011",
        channel="telegram",
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adeyemi",
                    "recipient_bank_name": "GTBank",
                    "recipient_account": "2010000002",
                    "transaction_id": "tx-final-123",
                    "receipt": {"status": "success"},
                },
            )
        },
    )
    finalize_updates = await finalize(finalize_state, _config(_SurfaceFollowupPlanner(ContextFrameFollowupDecision())))
    frame = finalize_updates["context_frames"][-1]
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(decision="show_details", confidence=0.95, detected_language="English")
    )
    state = OrchestratorState(
        user_id="u_surface_receipt_ref",
        phone_number="2348000000011",
        channel="telegram",
        last_message_text="what was the reference?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Receipt Details" in updates["final_response"]
    assert "Reference: tx-final-123" in updates["final_response"]
    assert "Tolu Adeyemi" in updates["final_response"]


@pytest.mark.asyncio
async def test_completed_mixed_transaction_followup_answers_airtime_number_from_frame() -> None:
    finalize_state = OrchestratorState(
        user_id="u_surface_mixed_airtime",
        phone_number="2348000000012",
        channel="telegram",
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adeyemi",
                    "recipient_bank_name": "GTBank",
                    "recipient_account": "2010000002",
                    "receipt": {"status": "processing"},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                    "receipt": {"status": "queued"},
                },
            ),
        },
    )
    finalize_updates = await finalize(finalize_state, _config(_SurfaceFollowupPlanner(ContextFrameFollowupDecision())))
    frame = finalize_updates["context_frames"][-1]
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.94,
            detected_language="English",
            reference_text="airtime",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_mixed_airtime",
        phone_number="2348000000012",
        channel="telegram",
        last_message_text="what was the airtime number?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transaction Details" in updates["final_response"]
    assert "08162511023" in updates["final_response"]
    assert "MTN" in updates["final_response"]
    assert "Tolu Adeyemi" not in updates["final_response"]


@pytest.mark.asyncio
async def test_completed_mixed_transaction_replay_rebuilds_all_tasks_from_frame() -> None:
    finalize_state = OrchestratorState(
        user_id="u_surface_mixed_replay",
        phone_number="2348000000014",
        channel="telegram",
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "recipient_account": "2010000001",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                    "receipt": {"status": "processing"},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                    "receipt": {"status": "queued"},
                },
            ),
        },
    )
    finalize_updates = await finalize(finalize_state, _config(_SurfaceFollowupPlanner(ContextFrameFollowupDecision())))
    frame = finalize_updates["context_frames"][-1]
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(decision="replay_tasks", confidence=0.96, detected_language="English")
    )
    state = OrchestratorState(
        user_id="u_surface_mixed_replay",
        phone_number="2348000000014",
        channel="telegram",
        last_message_text="Do again",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert "final_response" not in updates
    assert updates.get("semantic_path_shape") == "context_frame_replay"
    assert {task.type for task in updates["tasks"].values()} == {"transfer", "airtime"}
    assert updates["waves"] == [list(updates["tasks"].keys())]
    transfer_task = next(task for task in updates["tasks"].values() if task.type == "transfer")
    airtime_task = next(task for task in updates["tasks"].values() if task.type == "airtime")
    assert transfer_task.payload["amount"] == 2000
    assert transfer_task.payload["recipient_account"] == "2010000001"
    assert transfer_task.payload["recipient_bank_code"] == "044"
    assert transfer_task.payload["source_account_id"] == "acct-access"
    assert transfer_task.payload["source_account_number"] == "9000000003"
    assert transfer_task.payload["skip_extraction"] is True
    assert airtime_task.payload["amount"] == 1000
    assert airtime_task.payload["recipient_phone"] == "08162511023"
    assert airtime_task.payload["network"] == "MTN"
    assert airtime_task.payload["source_account_id"] == "acct-access"
    assert airtime_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_completed_transaction_replay_restores_source_account_number_from_loaded_accounts() -> None:
    finalize_state = OrchestratorState(
        user_id="u_surface_replay_source",
        phone_number="2348000000016",
        channel="telegram",
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "recipient_account": "2010000001",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "receipt": {"status": "success"},
                },
            ),
        },
    )
    finalize_updates = await finalize(finalize_state, _config(_SurfaceFollowupPlanner(ContextFrameFollowupDecision())))
    frame = finalize_updates["context_frames"][-1]
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(decision="replay_tasks", confidence=0.96, detected_language="English")
    )
    state = OrchestratorState(
        user_id="u_surface_replay_source",
        phone_number="2348000000016",
        channel="telegram",
        last_message_text="Send again",
        loaded_context={
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                }
            ]
        },
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(task for task in updates["tasks"].values() if task.type == "transfer")
    assert transfer_task.payload["recipient_bank_code"] == "044"
    assert transfer_task.payload["source_account_id"] == "acct-access"
    assert transfer_task.payload["source_account_number"] == "9000000003"


@pytest.mark.asyncio
async def test_completed_mixed_transaction_replay_can_target_airtime_only() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_recent",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦2,000 transfer to Tolu",
                data={
                    "task_type": "transfer",
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                },
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-airtime",
                label="₦1,000 airtime for 08162511023",
                data={
                    "task_type": "airtime",
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                },
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="English",
            reference_text="airtime",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_airtime_replay",
        phone_number="2348000000015",
        channel="telegram",
        last_message_text="do the airtime again",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert {task.type for task in updates["tasks"].values()} == {"airtime"}
    airtime_task = next(iter(updates["tasks"].values()))
    assert airtime_task.payload["recipient_phone"] == "08162511023"


@pytest.mark.asyncio
async def test_fresh_request_after_surface_frame_routes_to_normal_planner() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        response="",
        response_key=None,
        confidence=0.96,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="send 5000 to tolu",
        tasks=[
            PlannedTask(
                task_id="t_transfer",
                action="send_money",
                executor="transfer",
                instruction="send 5000 to tolu",
                parameters=TaskParameters(amount=5000, recipient="tolu"),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(decision="start_new_task", confidence=0.97),
        planner_output=planner_output,
    )
    state = OrchestratorState(
        user_id="u_surface_fresh_task",
        phone_number="2348000000013",
        channel="telegram",
        last_message_text="send 5k to tolu",
        context_frames=[_transaction_list_frame()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 1
    assert "final_response" not in updates
    assert updates["tasks"]["t_transfer"].type == "transfer"
    assert updates["tasks"]["t_transfer"].payload["amount"] == 5000
    assert updates["tasks"]["t_transfer"].payload["recipient_name"] == "tolu"
