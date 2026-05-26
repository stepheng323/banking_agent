import time

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.finalize import finalize
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context_frame_stages import _stage_context_frame_followup
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameFollowupFilters,
    ContextFrameReplayModifier,
    PlannedTask,
    PlannerOutput,
    TaskParameters,
)


class _SurfaceFollowupPlanner:
    def __init__(
        self,
        decision: ContextFrameFollowupDecision,
        planner_output: PlannerOutput | None = None,
        replay_modifier: ContextFrameReplayModifier | None = None,
    ) -> None:
        self.decision = decision
        self.planner_output = planner_output
        self.replay_modifier = replay_modifier
        self.last_frame_context: str | None = None
        self.last_replay_modifier_context: str | None = None
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

    async def extract_context_frame_replay_modifiers(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameReplayModifier | None:
        del phone_number, text, path_label
        self.last_replay_modifier_context = context
        return self.replay_modifier

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


def _transaction_list_frame_with_three_items() -> ContextFrame:
    frame = _transaction_list_frame()
    items = [
        *frame.items,
        ContextEntity(
            entity_type=EntityType.TRANSACTION,
            entity_id="tx-3",
            label="Transfer to Dad",
            data={
                "amount": 30000,
                "bank_name": "First Bank",
                "transaction_type": "debit",
                "status": "successful",
                "date": "2026-05-03",
                "reference": "tx-ref-3",
            },
        ),
    ]
    return frame.model_copy(update={"items": items})


def _transaction_list_frame_with_amount_reference() -> ContextFrame:
    now = int(time.time())
    return ContextFrame(
        frame_id="tx_list_amount_ref",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-interest",
                label="Bank Interest",
                data={
                    "amount": 1250,
                    "bank_name": "Zenith Bank",
                    "transaction_type": "credit",
                    "status": "successful",
                    "date": "2026-04-12",
                },
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-bakare",
                label="Transfer from Bakare Femi",
                data={
                    "amount": 20000,
                    "bank_name": "Zenith Bank",
                    "transaction_type": "credit",
                    "status": "successful",
                    "date": "2026-04-11",
                    "reference": "tx-ref-bakare",
                },
            ),
        ],
        created_at_ts=now,
        ttl_seconds=600,
    )


def _transaction_list_frame_with_25k_item() -> ContextFrame:
    now = int(time.time())
    return ContextFrame(
        frame_id="tx_list_25k_ref",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-mum",
                label="Transfer to Mum",
                data={
                    "amount": 50000,
                    "bank_name": "Zenith Bank",
                    "transaction_type": "debit",
                    "status": "successful",
                    "date": "2026-05-07",
                },
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-adebayo",
                label="Transfer to Adebayo James",
                data={
                    "amount": 25000,
                    "bank_name": "Zenith Bank",
                    "transaction_type": "debit",
                    "status": "successful",
                    "date": "2026-05-06",
                    "reference": "tx-ref-adebayo",
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
async def test_transaction_surface_selection_promotes_detail_frame_for_pronoun_followup() -> None:
    first_planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="select_item",
            confidence=0.94,
            selection_index=2,
            detected_language="English",
        )
    )
    first_state = OrchestratorState(
        user_id="u_surface_tx_chained_field",
        phone_number="2348000000030",
        channel="telegram",
        last_message_text="show the second one",
        context_frames=[_transaction_list_frame()],
    )

    first_updates = await plan_tasks(first_state, _config(first_planner))

    promoted_frame = first_updates["context_frames"][-1]
    assert promoted_frame.frame_type == ContextFrameType.TRANSACTION_DETAIL
    assert len(promoted_frame.items) == 1
    assert promoted_frame.items[0].label == "Transfer to Tolu"

    second_planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            requested_field="bank",
            detected_language="English",
        )
    )
    second_state = OrchestratorState(
        user_id="u_surface_tx_chained_field",
        phone_number="2348000000030",
        channel="telegram",
        last_message_text="what bank was that?",
        context_frames=first_updates["context_frames"],
    )

    second_updates = await plan_tasks(second_state, _config(second_planner))

    assert second_planner.plan_calls == 0
    assert second_updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Bank: Access Bank" in second_updates["final_response"]
    assert "Credit from Ada" not in second_updates["final_response"]
    assert "Which" not in second_updates["final_response"]


@pytest.mark.asyncio
async def test_transaction_surface_can_reference_older_list_after_detail_focus() -> None:
    first_planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="select_item",
            confidence=0.94,
            selection_index=2,
            detected_language="English",
        )
    )
    first_state = OrchestratorState(
        user_id="u_surface_tx_chained_back_to_list",
        phone_number="2348000000031",
        channel="telegram",
        last_message_text="show the second one",
        context_frames=[_transaction_list_frame_with_three_items()],
    )

    first_updates = await plan_tasks(first_state, _config(first_planner))

    second_planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="select_item",
            confidence=0.94,
            selection_index=3,
            detected_language="English",
        )
    )
    second_state = OrchestratorState(
        user_id="u_surface_tx_chained_back_to_list",
        phone_number="2348000000031",
        channel="telegram",
        last_message_text="now show the 3rd transaction",
        context_frames=first_updates["context_frames"],
    )

    second_updates = await plan_tasks(second_state, _config(second_planner))

    assert second_planner.plan_calls == 0
    assert "Current focus:" in (second_planner.last_frame_context or "")
    assert "Earlier result 1:" in (second_planner.last_frame_context or "")
    assert second_updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transfer to Dad" in second_updates["final_response"]
    assert "First Bank" in second_updates["final_response"]
    assert "tx-ref-3" in second_updates["final_response"]
    assert "Transfer to Tolu" not in second_updates["final_response"]


@pytest.mark.asyncio
async def test_transaction_surface_followup_selects_visible_amount_reference() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            target_text="20k",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_amount_ref",
        phone_number="2348000000032",
        channel="telegram",
        last_message_text="show the details of the 20k one",
        context_frames=[_transaction_list_frame_with_amount_reference()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transaction Details" in updates["final_response"]
    assert "Transfer from Bakare Femi" in updates["final_response"]
    assert "Amount: 20000" in updates["final_response"]
    assert "Bank Interest" not in updates["final_response"]


@pytest.mark.asyncio
async def test_unclear_transaction_surface_followup_still_grounds_visible_amount_reference() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="unclear",
            confidence=0.82,
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_unclear_amount_ref",
        phone_number="2348000000033",
        channel="telegram",
        last_message_text="I mean the 25k one",
        context_frames=[_transaction_list_frame_with_25k_item()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transaction Details" in updates["final_response"]
    assert "Transfer to Dad" not in updates["final_response"]
    assert "Adebayo James" in updates["final_response"]
    assert "Amount: 25000" in updates["final_response"]


@pytest.mark.asyncio
async def test_unclear_transaction_surface_followup_reports_missing_visible_amount_reference() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="unclear",
            confidence=0.82,
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_unclear_missing_amount_ref",
        phone_number="2348000000034",
        channel="telegram",
        last_message_text="what is the 24k one for",
        context_frames=[_transaction_list_frame_with_three_items()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert updates["final_response"] == "I don't see ₦24,000 in the transactions or results I showed."


@pytest.mark.asyncio
async def test_transaction_surface_followup_does_not_fallback_when_amount_is_missing() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_missing_amount_detail",
        phone_number="2348000000035",
        channel="telegram",
        last_message_text="show the details of the 20k one",
        context_frames=[_transaction_list_frame_with_25k_item()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert updates["final_response"] == "I don't see ₦20,000 in the transactions or results I showed."
    assert "Adebayo James" not in updates["final_response"]


@pytest.mark.asyncio
async def test_transaction_surface_followup_answers_selected_field_only() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            selection_index=2,
            requested_field="bank",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_field",
        phone_number="2348000000017",
        channel="telegram",
        last_message_text="what bank was the second one?",
        context_frames=[_transaction_list_frame()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transfer to Tolu" in updates["final_response"]
    assert "Bank: Access Bank" in updates["final_response"]
    assert "Amount:" not in updates["final_response"]
    assert "Credit from Ada" not in updates["final_response"]


@pytest.mark.asyncio
async def test_transaction_surface_followup_answers_typed_selected_field_only() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            selection_index=2,
            requested_field="bank",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_typed_field",
        phone_number="2348000000019",
        channel="telegram",
        last_message_text="what bank was the second one?",
        context_frames=[_transaction_list_frame()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Transfer to Tolu" in updates["final_response"]
    assert "Bank: Access Bank" in updates["final_response"]
    assert "Amount:" not in updates["final_response"]
    assert "Credit from Ada" not in updates["final_response"]


@pytest.mark.asyncio
async def test_transaction_surface_followup_selects_largest_result() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.94,
            rank="largest",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_largest",
        phone_number="2348000000018",
        channel="telegram",
        last_message_text="show the largest",
        context_frames=[_transaction_list_frame()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Credit from Ada" in updates["final_response"]
    assert "Amount: 5000" in updates["final_response"]
    assert "Transfer to Tolu" not in updates["final_response"]


@pytest.mark.asyncio
async def test_transaction_surface_followup_selects_typed_largest_result() -> None:
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.94,
            rank="largest",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_tx_typed_largest",
        phone_number="2348000000020",
        channel="telegram",
        last_message_text="show the largest",
        context_frames=[_transaction_list_frame()],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Credit from Ada" in updates["final_response"]
    assert "Amount: 5000" in updates["final_response"]
    assert "Transfer to Tolu" not in updates["final_response"]


@pytest.mark.asyncio
async def test_grouped_surface_followup_filters_by_typed_transaction_type() -> None:
    frame = ContextFrame(
        frame_id="grouped_tx_recent",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="group-credit",
                label="Credits",
                data={"amount": 25000, "count": 3, "transaction_type": "credit", "bank_name": "GTBank"},
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="group-debit",
                label="Debits",
                data={"amount": 12000, "count": 4, "transaction_type": "debit", "bank_name": "Access Bank"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.95,
            filters=ContextFrameFollowupFilters(transaction_type="credit"),
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_grouped_filter",
        phone_number="2348000000021",
        channel="telegram",
        last_message_text="what about credits?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Credits" in updates["final_response"]
    assert "Amount: 25000" in updates["final_response"]
    assert "Debits" not in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_filters_by_typed_bank() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-first",
                label="First Bank account",
                data={"bank_name": "First Bank", "account_number": "6000000001", "mandate_status": "pending"},
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank account",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="filter_items",
            confidence=0.95,
            filters=ContextFrameFollowupFilters(bank="GTBank"),
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_bank_filter",
        phone_number="2348000000022",
        channel="telegram",
        last_message_text="which one is GTBank?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "GTBank account" in updates["final_response"]
    assert "Mandate Status: ready" in updates["final_response"]
    assert "First Bank account" not in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_matches_bank_alias_from_target_text() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent_alias",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-first",
                label="First Bank (...0001)",
                data={"bank_name": "First Bank", "account_number": "6000000001", "mandate_status": "ready"},
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank (...0002)",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.95,
            target_text="the gtb",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_gtb_alias",
        phone_number="2348000000023",
        channel="telegram",
        last_message_text="Show the full details of the Gtb",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "GTBank (...0002)" in updates["final_response"]
    assert "Account Number: 7000000002" in updates["final_response"]
    assert "First Bank (...0001)" not in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_matches_spaced_gt_bank_alias() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent_spaced_gt_alias",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-first",
                label="First Bank (...0001)",
                data={"bank_name": "First Bank", "account_number": "6000000001", "mandate_status": "ready"},
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank (...0002)",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="lookup_entity",
            confidence=0.95,
            target_text="gt bank",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_gt_bank_alias",
        phone_number="2348000000027",
        channel="telegram",
        last_message_text="Which one is gtb?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "GTBank (...0002)" in updates["final_response"]
    assert "First Bank (...0001)" not in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_matches_pending_status_from_target_text() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent_pending",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-zenith",
                label="Zenith Bank (...9384)",
                data={"bank_name": "Zenith Bank", "account_number": "1234509384", "mandate_status": "pending"},
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank (...0002)",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="lookup_entity",
            confidence=0.95,
            target_text="Zenith pending",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_pending",
        phone_number="2348000000024",
        channel="telegram",
        last_message_text="Why is zenith bank pending?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Zenith Bank (...9384)" in updates["final_response"]
    assert "Mandate Status: pending" in updates["final_response"]
    assert "I don't see" not in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_explains_pending_account_status() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent_pending_explain",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-zenith",
                label="Zenith Bank (...9384)",
                data={"bank_name": "Zenith Bank", "account_number": "1234509384", "mandate_status": "pending"},
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank (...0002)",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="explain_result",
            confidence=0.95,
            target_text="Zenith Bank",
            requested_field="status",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_pending_explain",
        phone_number="2348000000025",
        channel="telegram",
        last_message_text="Why is zenith still pending?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Zenith Bank (...9384) is still pending" in updates["final_response"]
    assert "Mandate Status: pending" in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_explains_how_to_complete_pending_mandate() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent_pending_completion",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-zenith",
                label="Zenith Bank (...9384)",
                data={
                    "bank_name": "Zenith Bank",
                    "account_number": "1234509384",
                    "mandate_status": "pending",
                    "extra_data": {
                        "transfer_destinations": [
                            {"bank_name": "NIBSS Bank", "account_number": "0001112223"},
                            {"bank_name": "Test Bank", "account_number": "9998887776"},
                        ]
                    },
                },
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank (...0002)",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="explain_result",
            confidence=0.95,
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_pending_completion",
        phone_number="2348000000028",
        channel="telegram",
        last_message_text="How do I complete it?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "transfer ₦50 from your Zenith Bank account ending in 9384" in updates["final_response"]
    assert "NIBSS Bank: 0001112223" in updates["final_response"]
    assert "Once the bank/NIBSS confirms it" in updates["final_response"]


@pytest.mark.parametrize(
    ("status", "expected", "instruction"),
    [
        ("awaiting_authorization", "account authorization is not complete yet", "make the ₦50 authorization transfer"),
        ("approved", "waiting for final readiness checks", "wait for NIBSS/bank verification"),
        ("ready", "is ready for transactions", "no action needed"),
        ("rejected", "authorization was rejected", "contact support or restart account authorization"),
        ("cancelled", "authorization was cancelled", "reinitiate account authorization"),
        ("expired", "authorization expired", "restart account authorization"),
        ("paused", "authorization is paused", "contact support to reinstate"),
        ("provider_review", "has mandate status: provider_review", ""),
    ],
)
@pytest.mark.asyncio
async def test_account_surface_followup_explains_known_mandate_statuses(
    status: str,
    expected: str,
    instruction: str,
) -> None:
    frame = ContextFrame(
        frame_id=f"accounts_recent_status_{status}",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-status",
                label="Zenith Bank (...9384)",
                data={"bank_name": "Zenith Bank", "account_number": "1234509384", "mandate_status": status},
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="explain_result",
            confidence=0.95,
            target_text="Zenith",
            requested_field="status",
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id=f"u_surface_account_status_{status}",
        phone_number="2348000000029",
        channel="telegram",
        last_message_text="what is the status?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert expected in updates["final_response"]
    assert status in updates["final_response"]
    if instruction:
        assert instruction in updates["final_response"]


@pytest.mark.asyncio
async def test_account_surface_followup_rescues_status_question_misclassified_as_new_task() -> None:
    frame = ContextFrame(
        frame_id="accounts_recent_pending_rescue",
        frame_type=ContextFrameType.ACCOUNT_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-zenith",
                label="Zenith Bank (...9384)",
                data={"bank_name": "Zenith Bank", "account_number": "1234509384", "mandate_status": "pending"},
            ),
            ContextEntity(
                entity_type=EntityType.ACCOUNT,
                entity_id="acct-gtb",
                label="GTBank (...0002)",
                data={"bank_name": "GTBank", "account_number": "7000000002", "mandate_status": "ready"},
            ),
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="start_new_task",
            confidence=0.92,
            detected_language="English",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_account_pending_rescue",
        phone_number="2348000000026",
        channel="telegram",
        last_message_text="Why is zenith still pending?",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    assert planner.plan_calls == 0
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "Zenith Bank (...9384) is still pending" in updates["final_response"]
    assert "Which transaction" not in updates["final_response"]


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
            target_text="airtime",
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
async def test_completed_transfer_replay_applies_amount_override() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_amount_override",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="English",
            target_text="10k",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_replay_override",
        phone_number="2348000000017",
        channel="whatsapp",
        last_message_text="Again, but with 10k",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 10000
    assert transfer_task.payload["recipient_account"] == "2010000001"
    assert transfer_task.payload["source_account_id"] == "acct-access"
    assert transfer_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_completed_transfer_replay_applies_source_account_override() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_source_override",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="English",
            target_text="Zenith",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_replay_source_override",
        phone_number="2348000000018",
        channel="whatsapp",
        last_message_text="Again, but from Zenith",
        loaded_context={
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                },
                {
                    "id": "acct-zenith",
                    "bank_name": "Zenith Bank",
                    "account_number": "8000009384",
                    "account_name": "Olamide Samuel",
                    "mandate_status": "ready",
                },
            ]
        },
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 20000
    assert transfer_task.payload["recipient_account"] == "2010000001"
    assert transfer_task.payload["source_account_id"] == "acct-zenith"
    assert transfer_task.payload["source_bank_name"] == "Zenith Bank"
    assert transfer_task.payload["source_account_number"] == "8000009384"
    assert transfer_task.payload["source_account_name"] == "Olamide Samuel"
    assert transfer_task.payload["source_account_index"] is None
    assert transfer_task.payload["source_affinity_mode"] == "explicit"
    assert transfer_task.payload["funding_plan"] is None
    assert transfer_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_completed_transfer_replay_applies_source_account_override_from_transaction_accounts() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_source_override_transaction_accounts",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦10,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="English",
            target_text="gtb",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_replay_source_override_transaction_accounts",
        phone_number="2348000000023",
        channel="whatsapp",
        last_message_text="Again, from gtb",
        loaded_context={
            "transaction_accounts": [
                {
                    "id": "acct-access",
                    "bank": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                },
                {
                    "id": "acct-gtb",
                    "bank": "GTBank",
                    "account_number": "9000000002",
                    "account_name": "Olamide Samuel",
                    "mandate_status": "ready",
                },
            ]
        },
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 10000
    assert transfer_task.payload["recipient_account"] == "2010000001"
    assert transfer_task.payload["source_account_id"] == "acct-gtb"
    assert transfer_task.payload["source_bank_name"] == "GTBank"
    assert transfer_task.payload["source_account_number"] == "9000000002"
    assert transfer_task.payload["source_affinity_mode"] == "explicit"


@pytest.mark.asyncio
async def test_completed_transfer_replay_blocks_unmatched_explicit_source() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_source_unmatched",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦10,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="English",
            target_text="gtb",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_replay_source_unmatched",
        phone_number="2348000000024",
        channel="whatsapp",
        last_message_text="Again, from gtb",
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

    assert "tasks" not in updates
    assert "could not find 'gtb'" in updates["final_response"]
    assert updates["semantic_path_shape"] == "context_frame_replay_source_unmatched"


@pytest.mark.asyncio
async def test_completed_transfer_replay_applies_narration_override() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_narration_override",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                    "narration": "old note",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="English",
            target_text="rent",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_replay_narration_override",
        phone_number="2348000000019",
        channel="whatsapp",
        last_message_text="Again, but for rent",
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 20000
    assert transfer_task.payload["recipient_account"] == "2010000001"
    assert transfer_task.payload["narration"] == "rent"
    assert transfer_task.payload["authored_narration"] == "rent"
    assert transfer_task.payload["user_note"] == "rent"
    assert transfer_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_completed_transfer_replay_accepts_multilingual_source_and_narration_markers() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_multilingual_modifiers",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="Yoruba",
            target_text="rent",
        )
    )
    state = OrchestratorState(
        user_id="u_surface_replay_multilingual_modifiers",
        phone_number="2348000000020",
        channel="whatsapp",
        last_message_text="Again, lati Zenith fun rent",
        loaded_context={
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                },
                {
                    "id": "acct-zenith",
                    "bank_name": "Zenith Bank",
                    "account_number": "8000009384",
                    "account_name": "Olamide Samuel",
                    "mandate_status": "ready",
                },
            ]
        },
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["source_account_id"] == "acct-zenith"
    assert transfer_task.payload["source_bank_name"] == "Zenith Bank"
    assert transfer_task.payload["narration"] == "rent"
    assert transfer_task.payload["authored_narration"] == "rent"
    assert transfer_task.payload["user_note"] == "rent"


@pytest.mark.asyncio
async def test_completed_transfer_replay_applies_structured_modifier_extraction() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_structured_modifier",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                    "narration": "old note",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    modifier = ContextFrameReplayModifier(
        confidence=0.91,
        detected_language="French",
        amount=10000,
        amount_evidence="dix mille",
        source_account_reference="Zenith",
        source_account_evidence="Zenith",
        narration="loyer",
        narration_evidence="loyer",
        reason="French replay modifiers",
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="French",
            target_text="loyer",
        ),
        replay_modifier=modifier,
    )
    state = OrchestratorState(
        user_id="u_surface_replay_structured_modifier",
        phone_number="2348000000021",
        channel="whatsapp",
        last_message_text="Encore avec dix mille depuis Zenith pour loyer",
        loaded_context={
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                },
                {
                    "id": "acct-zenith",
                    "bank_name": "Zenith Bank",
                    "account_number": "8000009384",
                    "account_name": "Olamide Samuel",
                    "mandate_status": "ready",
                },
            ]
        },
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 10000
    assert transfer_task.payload["source_account_id"] == "acct-zenith"
    assert transfer_task.payload["source_bank_name"] == "Zenith Bank"
    assert transfer_task.payload["narration"] == "loyer"
    assert transfer_task.payload["authored_narration"] == "loyer"
    assert transfer_task.payload["user_note"] == "loyer"
    assert transfer_task.payload["recipient_account"] == "2010000001"
    assert planner.last_replay_modifier_context is not None


@pytest.mark.asyncio
async def test_gate_context_frame_display_shortcut_avoids_llm_for_schedule_show_me() -> None:
    frame = ContextFrame(
        frame_id="schedule_list_1",
        frame_type=ContextFrameType.SCHEDULE_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.GENERIC,
                entity_id="sch-1",
                label="Transfer: ₦20,000 FATIMA ZAHRA MUSA • One Time at 2:00 PM Lagos time",
                data={
                    "type": "scheduled_transaction",
                    "schedule_id": "sch-1",
                    "domain": "Transfer",
                    "amount": "₦20,000",
                    "target": "FATIMA ZAHRA MUSA",
                    "recurrence": "One Time",
                    "schedule_time": "2:00 PM Lagos time",
                    "status": "active",
                },
            )
        ],
        created_at_ts=int(time.time()),
    )
    planner = _SurfaceFollowupPlanner(ContextFrameFollowupDecision(decision="unclear", confidence=0.0))
    state = OrchestratorState(
        user_id="u_schedule_show_gate",
        phone_number="2348000000026",
        channel="telegram",
        last_message_text="show me",
        context_frames=[frame],
    )
    ctx = GateContext(
        state=state,
        config=_config(planner),
        redis_client=None,
        task_planner=planner,
        conversation_responder=None,
        message_text=state.last_message_text or "",
        current_locale="en",
        gate_updates={},
        live_pending_interrupt=False,
        phrase_heavy_fastpath_allowed=True,
    )

    updates = await _stage_context_frame_followup(ctx)

    assert updates is not None
    assert updates["final_response"]
    assert "Scheduled Transaction Details" in updates["final_response"]
    assert "FATIMA ZAHRA MUSA" in updates["final_response"]
    assert "Target:" not in updates["final_response"]
    assert planner.last_frame_context is None


@pytest.mark.asyncio
async def test_gate_context_frame_display_formats_data_plan_details_naturally() -> None:
    frame = ContextFrame(
        frame_id="data_plan_details",
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_type=EntityType.DATA_PLAN,
                entity_id="MD501",
                label="MTN 5 GB data bundle",
                data={
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "network": "MTN",
                    "amount": 3500.0,
                    "validity_days": 30,
                },
            ),
            ContextEntity(
                entity_type=EntityType.DATA_PLAN,
                entity_id="MD502",
                label="MTN 5 GB data bundle",
                data={
                    "plan_code": "MD502",
                    "plan_name": "MTN 5 GB data bundle",
                    "network": "MTN",
                    "amount": 3500.0,
                    "validity_days": 30,
                },
            ),
        ],
        created_at_ts=int(time.time()),
    )
    planner = _SurfaceFollowupPlanner(ContextFrameFollowupDecision(decision="unclear", confidence=0.0))
    state = OrchestratorState(
        user_id="u_data_plan_details_gate",
        phone_number="2348000000026",
        channel="telegram",
        last_message_text="details",
        context_frames=[frame],
    )
    ctx = GateContext(
        state=state,
        config=_config(planner),
        redis_client=None,
        task_planner=planner,
        conversation_responder=None,
        message_text=state.last_message_text or "",
        current_locale="en",
        gate_updates={},
        live_pending_interrupt=False,
        phrase_heavy_fastpath_allowed=True,
    )

    updates = await _stage_context_frame_followup(ctx)

    assert updates is not None
    response = updates["final_response"]
    assert "Data Plan Details" in response
    assert "MTN 5 GB data bundle is ₦3,500, valid for 30 days." in response
    assert response.count("MTN 5 GB data bundle") == 1
    assert "Amount: 3500.0" not in response
    assert planner.last_frame_context is None


@pytest.mark.asyncio
async def test_gate_context_frame_replay_applies_structured_modifier_extraction() -> None:
    frame = ContextFrame(
        frame_id="tx_gate_replay_structured_modifier",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                    "narration": "old note",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    modifier = ContextFrameReplayModifier(
        confidence=0.91,
        detected_language="French",
        amount=10000,
        amount_evidence="dix mille",
        source_account_reference="gtb",
        source_account_evidence="gtb",
        narration="loyer",
        narration_evidence="loyer",
        reason="French replay modifiers",
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="replay_tasks",
            confidence=0.96,
            detected_language="French",
            target_text="loyer",
        ),
        replay_modifier=modifier,
    )
    state = OrchestratorState(
        user_id="u_gate_replay_structured_modifier",
        phone_number="2348000000025",
        channel="whatsapp",
        last_message_text="Encore avec dix mille depuis gtb pour loyer",
        stashed_query_session={
            "session_active": True,
            "query_result": {"summary_text": "Recent transaction results are still open."},
        },
        loaded_context={
            "transaction_accounts": [
                {
                    "id": "acct-access",
                    "bank": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                },
                {
                    "id": "acct-gtb",
                    "bank": "GTBank",
                    "account_number": "9000000002",
                    "account_name": "Olamide Samuel",
                    "mandate_status": "ready",
                },
            ],
        },
        context_frames=[frame],
    )
    ctx = GateContext(
        state=state,
        config=_config(planner),
        redis_client=None,
        task_planner=planner,
        conversation_responder=None,
        message_text=state.last_message_text,
        current_locale="en",
        gate_updates={},
        live_pending_interrupt=False,
        phrase_heavy_fastpath_allowed=True,
    )

    updates = await _stage_context_frame_followup(ctx)

    assert updates is not None
    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 10000
    assert transfer_task.payload["source_account_id"] == "acct-gtb"
    assert transfer_task.payload["source_bank_name"] == "GTBank"
    assert transfer_task.payload["source_account_number"] == "9000000002"
    assert transfer_task.payload["narration"] == "loyer"
    assert transfer_task.payload["authored_narration"] == "loyer"
    assert transfer_task.payload["user_note"] == "loyer"
    assert planner.last_replay_modifier_context is not None


@pytest.mark.asyncio
async def test_completed_transfer_replay_ignores_low_confidence_modifier_extraction() -> None:
    frame = ContextFrame(
        frame_id="tx_replay_low_confidence_modifier",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-transfer",
                label="₦20,000 transfer to Tolu Adebayo",
                data={
                    "task_type": "transfer",
                    "amount": 20000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "recipient_bank_code": "044",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "9000000003",
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )
    planner = _SurfaceFollowupPlanner(
        ContextFrameFollowupDecision(decision="replay_tasks", confidence=0.96, detected_language="English"),
        replay_modifier=ContextFrameReplayModifier(
            confidence=0.4,
            amount=10000,
            amount_evidence="again",
            source_account_reference="Zenith",
            source_account_evidence="again",
            narration="rent",
            narration_evidence="again",
        ),
    )
    state = OrchestratorState(
        user_id="u_surface_replay_low_confidence_modifier",
        phone_number="2348000000022",
        channel="whatsapp",
        last_message_text="Again",
        loaded_context={
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "9000000003",
                    "mandate_status": "ready",
                },
                {
                    "id": "acct-zenith",
                    "bank_name": "Zenith Bank",
                    "account_number": "8000009384",
                    "mandate_status": "ready",
                },
            ]
        },
        context_frames=[frame],
    )

    updates = await plan_tasks(state, _config(planner))

    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload["amount"] == 20000
    assert transfer_task.payload["source_account_id"] == "acct-access"
    assert transfer_task.payload.get("narration") != "rent"


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
            target_text="airtime",
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
