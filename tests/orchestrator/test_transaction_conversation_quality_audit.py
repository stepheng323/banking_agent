from __future__ import annotations

import time
from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import UnsupportedBoundaryTurnOutput
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import (
    ActiveSession,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
)
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary, OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.gate.core.node import session_gate_direct_path
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from banking.bills.airtime.worker import AirtimeWorker
from banking.bills.data.models.extraction import DataExtractionResult, DataPurchaseEntities
from banking.bills.data.worker import DataWorker
from banking.runtime.results import AccountOutcome, AccountResult
from shared.types.planner import PendingActionEditDecision, SemanticRouteDecision
from tests.orchestrator.conversation_harness import (
    ConversationScenario,
    ConversationTurn,
    run_conversation_scenario,
)


class _AuditPlanner:
    def __init__(
        self,
        *,
        route_decision: SemanticRouteDecision | None = None,
        boundary_decision: UnsupportedBoundaryTurnOutput | None = None,
    ) -> None:
        self.route_decision = route_decision or SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.91,
            response="semantic router fallback",
            expected_transaction_executors=[],
            reason="audit fallback route",
        )
        self.boundary_decision = boundary_decision
        self.route_calls = 0
        self.boundary_calls = 0

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.route_calls += 1
        return self.route_decision

    async def classify_unsupported_boundary_turn(
        self, *args: object, **kwargs: object
    ) -> UnsupportedBoundaryTurnOutput:
        del args, kwargs
        self.boundary_calls += 1
        return self.boundary_decision or UnsupportedBoundaryTurnOutput(
            action="unclear",
            capability_key=None,
            confidence=0.2,
            reason="no audit boundary decision configured",
        )


class _PendingEditAuditPlanner:
    def __init__(self, decision: PendingActionEditDecision) -> None:
        self.decision = decision
        self.edit_calls = 0
        self.route_calls = 0

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        self.edit_calls += 1
        return self.decision

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.route_calls += 1
        return SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.2,
            response="semantic fallback should not be used",
            expected_transaction_executors=[],
            reason="pending edit audit fallback",
        )


class _EmptyDataExtractor:
    async def extract(self, text: str, smart_context: dict[str, Any] | None = None) -> DataExtractionResult:
        del text, smart_context
        return DataExtractionResult(entities=DataPurchaseEntities())


class _QualityDataExtractor:
    async def extract(self, text: str, smart_context: dict[str, Any] | None = None) -> DataExtractionResult:
        del smart_context
        message = text.lower()
        entities: dict[str, Any] = {}
        if "airtel" in message:
            entities["network"] = "AIRTEL"
        elif "mtn" in message:
            entities["network"] = "MTN"
        if "for me" in message or "my line" in message or "buy me" in message:
            entities["is_self"] = True
        if "4k" in message or "4000" in message:
            entities["budget"] = 4000
        elif "2k" in message or "2000" in message:
            entities["budget"] = 2000
        elif "5gb" in message or "5 gb" in message:
            entities["size_preference"] = "5GB"
        return DataExtractionResult(entities=DataPurchaseEntities(**entities))


class _QualityDataBillProvider:
    async def get_data_plans(self, network: str) -> dict[str, Any]:
        if str(network or "").strip().lower() == "airtel":
            return {
                "success": True,
                "plans": [
                    {
                        "item_code": "AD400",
                        "biller_code": "BIL106",
                        "biller_name": "AIRTEL 9GB data bundle",
                        "short_name": "AIRTEL 9GB data bundle",
                        "amount": 4000,
                        "validity_period": "30",
                    },
                    {
                        "item_code": "AD130",
                        "biller_code": "BIL106",
                        "biller_name": "AIRTEL 3 GB Monthly",
                        "short_name": "AIRTEL 3 GB Monthly",
                        "amount": 1500,
                        "validity_period": "30",
                    },
                ],
            }
        return {
            "success": True,
            "plans": [
                {
                    "item_code": "MD501",
                    "biller_code": "BIL104",
                    "biller_name": "MTN 5 GB data bundle",
                    "short_name": "MTN 5 GB data bundle",
                    "amount": 3500,
                    "validity_period": "30",
                },
                {
                    "item_code": "MD108",
                    "biller_code": "BIL104",
                    "biller_name": "MTN 3.5 GB",
                    "short_name": "MTN 3.5 GB",
                    "amount": 2000,
                    "validity_period": "30",
                },
                {
                    "item_code": "MD107",
                    "biller_code": "BIL104",
                    "biller_name": "MTN 1.5 GB",
                    "short_name": "MTN 1.5 GB",
                    "amount": 1000,
                    "validity_period": "30",
                },
            ],
        }


class _QualityAirtimeExtractor:
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        message = str(state.get("message") or "").lower()
        entities: dict[str, Any] = {}
        if "airtel" in message:
            entities["network"] = "Airtel"
        elif "mtn" in message:
            entities["network"] = "MTN"
        if "for me" in message or "my line" in message or "buy me" in message:
            entities["is_self"] = True
        if "1k" in message or "1000" in message:
            entities["amount"] = 1000
        if "2k" in message or "2000" in message:
            entities["amount"] = 2000
        return {"entities": entities}


class _QualityAccountBalanceWorker:
    def __init__(self) -> None:
        self.last_user_message: str | None = None

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> AccountResult:
        del payload, context, pin_verified
        self.last_user_message = user_message
        return AccountResult(
            outcome=AccountOutcome.OK,
            response="Your Access Bank account (···0003) has a balance of ₦30,000.00.",
        )


class _FailIfInterruptRouterCalledPlanner:
    async def route_pending_input(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("route_pending_input should not be called for structural slot replies")

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("route_semantic_turn should not be called for structural slot replies")


def _banking_context() -> dict[str, Any]:
    return {
        "language": "en",
        "accounts": [
            {
                "id": "acc_access",
                "bank_name": "Access Bank",
                "account_name": "Gaines",
                "account_number": "2010000003",
                "is_default": True,
                "mandate_status": "ready",
            }
        ],
    }


def _banking_context_with_gtbank() -> dict[str, Any]:
    context = _banking_context()
    context["accounts"] = [
        *context["accounts"],
        {
            "id": "acc_gtb",
            "bank_name": "GTBank",
            "account_name": "Gaines",
            "account_number": "2010000002",
            "is_default": False,
            "mandate_status": "ready",
        },
    ]
    return context


def _transaction_config(**services: Any) -> dict[str, Any]:
    return {"configurable": {"services": services, "redis_client": None}, "recursion_limit": 50}


def _apply_updates(state: OrchestratorState, updates: dict[str, Any]) -> OrchestratorState:
    return state.model_copy(update=updates)


def _next_user_turn(state: OrchestratorState, text: str) -> OrchestratorState:
    return state.model_copy(
        update={
            "last_message_text": text,
            "final_response": None,
            "policy_notice": None,
            "direct_path_triggered": False,
            "semantic_path_shape": None,
            "routing_owner": None,
            "routing_decision": None,
            "routing_target_domain": None,
            "routing_mode": None,
            "route_source": None,
            "routing_heuristic_type": None,
            "routing_heuristic_name": None,
            "planner_used": False,
            "suppress_empty_fallback": False,
        }
    )


def _state(
    *,
    user_id: str,
    text: str = "",
    capability_boundary: CapabilityBoundary | None = None,
    context_frames: list[ContextFrame] | None = None,
    stashed_sessions: list[dict[str, object]] | None = None,
) -> OrchestratorState:
    return OrchestratorState(
        user_id=user_id,
        phone_number=f"2348001{user_id[-6:]}",
        channel="whatsapp",
        last_message_text=text,
        loaded_context={"language": "en"},
        capability_boundary=capability_boundary,
        context_frames=context_frames or [],
        stashed_sessions=stashed_sessions or [],
    )


def _stale_transfer_frame() -> ContextFrame:
    return ContextFrame(
        frame_id="audit-stale-transfer",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-audit-stale",
                label="2,000 transfer to Tolu Adebayo",
                data={"task_type": "transfer", "amount": 2000, "recipient_name": "Tolu Adebayo"},
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=600,
    )


def _resume_prompt_frame() -> ContextFrame:
    return ContextFrame(
        frame_id="audit-resume-prompt",
        frame_type=ContextFrameType.GENERIC,
        items=[
            ContextEntity(
                entity_id="resume-prompt",
                entity_type=EntityType.GENERIC,
                label="Resume transfer",
                data={"resume_prompt": True, "stash_id": "stash-audit"},
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_quality_audit_data_price_query_to_buy_it_reuses_catalog_plan_to_confirmation() -> None:
    worker = DataWorker(
        extractor=_EmptyDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    config = _transaction_config(data=worker)
    state = OrchestratorState(
        user_id="u_quality_data_transcript",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="How much is 5GB MTN?",
        loaded_context=_banking_context(),
    )

    gate_updates = await session_gate_direct_path(state, config)
    assert gate_updates["semantic_path_shape"] == "deterministic_data_plan_query"
    query_task = next(iter(gate_updates["tasks"].values()))
    assert query_task.payload["action"] == "data_plan_query"

    state = _apply_updates(state, gate_updates)
    query_updates = await advance_wave(state, config)
    assert query_updates["outbox"] == [{"type": "say", "text": "MTN 5 GB data bundle is ₦3,500, valid for 30 days."}]
    state = _apply_updates(state, query_updates)
    assert state.context_frames[-1].frame_type == ContextFrameType.DATA_PLAN_LIST
    assert any(item.referent_type == "data_plan" for item in state.referent_memory.items)

    state = _next_user_turn(state, "Buy it")
    buy_gate_updates = await session_gate_direct_path(state, config)
    assert buy_gate_updates["semantic_path_shape"] == "data_plan_reference_purchase"
    buy_task = next(iter(buy_gate_updates["tasks"].values()))
    assert buy_task.type == "data"
    assert buy_task.payload["plan_code"] == "MD501"
    assert buy_task.payload["amount"] == 3500
    assert buy_task.payload["skip_extraction"] is True

    state = _apply_updates(state, buy_gate_updates)
    confirmation_updates = await advance_wave(state, config)
    assert isinstance(confirmation_updates["pending_interrupt"], PendingInterrupt)
    assert confirmation_updates["pending_interrupt"].kind == "confirmation"
    assert len(confirmation_updates["outbox"]) == 1
    confirmation = confirmation_updates["outbox"][0]
    assert confirmation["type"] == "request_confirmation"
    assert confirmation["header"] == "Review Data Purchase"
    assert "MTN 5 GB data bundle for your number (08162511023)" in confirmation["summary"]
    assert "Amount: ₦3,500" in confirmation["summary"]


@pytest.mark.asyncio
async def test_quality_audit_explicit_airtel_data_does_not_reuse_mtn_self_line() -> None:
    worker = DataWorker(
        extractor=_QualityDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    config = _transaction_config(data=worker)
    config["configurable"]["task_planner"] = _FailIfInterruptRouterCalledPlanner()
    state = OrchestratorState(
        user_id="u_quality_airtel_data_transcript",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="I want to buy Airtel data",
        loaded_context=_banking_context(),
    )

    gate_updates = await session_gate_direct_path(state, config)
    assert gate_updates["semantic_path_shape"] == "deterministic_data_domain"
    state = _apply_updates(state, gate_updates)

    input_updates = await advance_wave(state, config)
    task_id = next(iter(input_updates["tasks"]))
    assert input_updates["pending_interrupt"].kind == "input"
    assert input_updates["pending_interrupt"].fields_by_task == {task_id: ["target_phone", "data_plan_preference"]}
    assert input_updates["tasks"][task_id].payload["network"] == "AIRTEL"
    assert input_updates["tasks"][task_id].payload.get("target_phone") is None
    assert input_updates["outbox"][0]["text"] == (
        "Sure. Which Airtel line should I buy for, and what budget or data size should I use?"
    )
    assert "your Airtel line" not in input_updates["outbox"][0]["text"]
    assert "08162511023" not in input_updates["outbox"][0]["text"]
    state = _apply_updates(state, input_updates)

    state = _next_user_turn(state, "4k")
    preference_updates = await handle_pending_interrupt(state, config)
    assert preference_updates["pending_interrupt"] is None
    assert preference_updates["tasks"][task_id].stage == TaskStage.EXTRACTED
    state = _apply_updates(state, preference_updates)

    phone_prompt_updates = await advance_wave(state, config)
    assert phone_prompt_updates["pending_interrupt"].kind == "input"
    assert phone_prompt_updates["pending_interrupt"].fields_by_task == {task_id: ["target_phone"]}
    prompt = phone_prompt_updates["outbox"][0]
    assert prompt["type"] == "say"
    assert prompt["text"] == (
        "I found AIRTEL 9GB data bundle for ₦4,000, valid 30 days. Which Airtel line should I buy it for?"
    )
    assert "Confirm Data Purchase" not in prompt["text"]
    assert "08162511023" not in prompt["text"]


@pytest.mark.asyncio
async def test_quality_audit_airtime_self_flow_collects_amount_naturally_then_confirms() -> None:
    worker = AirtimeWorker(
        extractor=_QualityAirtimeExtractor(),
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
    )
    config = _transaction_config(airtime=worker)
    state = OrchestratorState(
        user_id="u_quality_airtime_transcript",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="buy airtime for me",
        loaded_context=_banking_context(),
    )

    gate_updates = await session_gate_direct_path(state, config)
    assert gate_updates["semantic_path_shape"] == "deterministic_airtime_domain"
    state = _apply_updates(state, gate_updates)

    input_updates = await advance_wave(state, config)
    assert input_updates["pending_interrupt"].kind == "input"
    assert input_updates["outbox"][0]["type"] == "say"
    assert input_updates["outbox"][0]["text"] == "Sure. I'll use your MTN line. How much airtime should I buy?"
    assert input_updates["outbox"][0]["prompt_kind"] == "pending_input"
    state = _apply_updates(state, input_updates)

    state = _next_user_turn(state, "1k")
    interrupt_updates = await handle_pending_interrupt(state, config)
    assert interrupt_updates["pending_interrupt"] is None
    state = _apply_updates(state, interrupt_updates)

    confirmation_updates = await advance_wave(state, config)
    assert confirmation_updates["pending_interrupt"].kind == "confirmation"
    assert len(confirmation_updates["outbox"]) == 1
    confirmation = confirmation_updates["outbox"][0]
    assert confirmation["type"] == "request_confirmation"
    assert confirmation["header"] == "Review Airtime Purchase"
    assert "₦1,000" in confirmation["summary"]
    assert "08066666700" in confirmation["summary"]


@pytest.mark.asyncio
async def test_quality_audit_airtime_composite_line_amount_prompt_accepts_amount_then_asks_line() -> None:
    worker = AirtimeWorker(
        extractor=_QualityAirtimeExtractor(),
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
    )
    config = _transaction_config(airtime=worker)
    config["configurable"]["task_planner"] = _FailIfInterruptRouterCalledPlanner()
    state = OrchestratorState(
        user_id="u_quality_airtime_composite",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="I want to buy Airtel airtime",
        loaded_context=_banking_context(),
    )

    gate_updates = await session_gate_direct_path(state, config)
    assert gate_updates["semantic_path_shape"] == "deterministic_airtime_domain"
    state = _apply_updates(state, gate_updates)

    input_updates = await advance_wave(state, config)
    assert input_updates["pending_interrupt"].kind == "input"
    assert input_updates["pending_interrupt"].fields_by_task == {
        next(iter(input_updates["tasks"])): ["recipient_phone", "amount"]
    }
    assert input_updates["outbox"][0]["text"] == "Sure. Which Airtel line should I buy airtime for, and how much?"
    state = _apply_updates(state, input_updates)

    state = _next_user_turn(state, "4k")
    interrupt_updates = await handle_pending_interrupt(state, config)
    assert interrupt_updates["pending_interrupt"] is None
    task_id = next(iter(interrupt_updates["tasks"]))
    assert interrupt_updates["tasks"][task_id].payload["network"] == "AIRTEL"
    assert "recipient_phone" not in interrupt_updates["tasks"][task_id].payload
    state = _apply_updates(state, interrupt_updates)

    line_updates = await advance_wave(state, config)
    assert line_updates["tasks"][task_id].payload["amount"] == 4000
    assert line_updates["tasks"][task_id].payload["network"] == "AIRTEL"
    assert line_updates["pending_interrupt"].kind == "input"
    assert line_updates["pending_interrupt"].fields_by_task == {task_id: ["recipient_phone"]}
    assert len(line_updates["outbox"]) == 1
    prompt = line_updates["outbox"][0]
    assert prompt["type"] == "say"
    assert prompt["prompt_kind"] == "pending_input"
    assert prompt["text"] == "Got ₦4,000.00 Airtel airtime. Which Airtel line should I buy it for?"
    assert "08162511023" not in prompt["text"]
    assert "Review Airtime Purchase" not in prompt["text"]


@pytest.mark.asyncio
async def test_quality_audit_airtime_confirmation_amount_edit_reconfirms_without_stale_amount() -> None:
    worker = AirtimeWorker(
        extractor=_QualityAirtimeExtractor(),
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="update_fields",
            confidence=0.95,
            detected_language="English",
            target_types=["airtime"],
            amount=2000,
            reason="user changed airtime amount",
        )
    )
    config = _transaction_config(airtime=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_airtime_edit",
        phone_number="2348066666700",
        channel="whatsapp",
        last_message_text="make it 2k",
        loaded_context=_banking_context(),
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["airtime_edit"]),
        tasks={
            "airtime_edit": TaskSpec(
                id="airtime_edit",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08066666700",
                    "phone": "08066666700",
                    "network": "MTN",
                    "is_self": True,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                    "confirmation": {
                        "summary": "Old airtime confirmation",
                        "snapshot": {"amount": 1000, "recipient_phone": "08066666700", "network": "MTN"},
                    },
                },
            )
        },
        waves=[["airtime_edit"]],
        current_wave_index=0,
    )

    interrupt_updates = await handle_pending_interrupt(state, config)
    assert interrupt_updates["pending_interrupt"] is None
    assert interrupt_updates["tasks"]["airtime_edit"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["airtime_edit"].payload["amount"] == 2000
    state = _apply_updates(state, interrupt_updates)

    confirmation_updates = await advance_wave(state, config)
    assert confirmation_updates["pending_interrupt"].kind == "confirmation"
    assert confirmation_updates["outbox"][0] == {"type": "say", "text": "Got it, updating airtime to ₦2,000."}
    confirmation = confirmation_updates["outbox"][1]
    assert confirmation["type"] == "request_confirmation"
    assert confirmation["header"] == "Review Airtime Purchase"
    assert "₦2,000" in confirmation["summary"]
    assert "₦1,000" not in confirmation["summary"]
    assert "08066666700" in confirmation["summary"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_airtime_network_edit_asks_for_matching_line_not_stale_self_number() -> None:
    worker = AirtimeWorker(
        extractor=_QualityAirtimeExtractor(),
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="update_fields",
            confidence=0.95,
            detected_language="English",
            target_types=["airtime"],
            network="Airtel",
            reason="user changed airtime network",
        )
    )
    config = _transaction_config(airtime=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_airtime_network_edit",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="change it to Airtel",
        loaded_context=_banking_context(),
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["airtime_network_edit"]),
        tasks={
            "airtime_network_edit": TaskSpec(
                id="airtime_network_edit",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "phone": "08162511023",
                    "network": "MTN",
                    "is_self": True,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                    "confirmation": {
                        "summary": "Old airtime confirmation",
                        "snapshot": {"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
                    },
                },
            )
        },
        waves=[["airtime_network_edit"]],
        current_wave_index=0,
    )

    interrupt_updates = await handle_pending_interrupt(state, config)
    patched_payload = interrupt_updates["tasks"]["airtime_network_edit"].payload
    assert interrupt_updates["pending_interrupt"] is None
    assert interrupt_updates["tasks"]["airtime_network_edit"].stage == TaskStage.EXTRACTED
    assert patched_payload["network"] == "Airtel"
    assert patched_payload["recipient_phone"] is None
    assert patched_payload["phone"] is None
    assert patched_payload["is_self"] is False
    state = _apply_updates(state, interrupt_updates)

    input_updates = await advance_wave(state, config)

    assert input_updates["pending_interrupt"].kind == "input"
    assert input_updates["pending_interrupt"].fields_by_task == {"airtime_network_edit": ["recipient_phone"]}
    assert len(input_updates["outbox"]) == 1
    prompt = input_updates["outbox"][0]
    assert prompt["type"] == "say"
    assert prompt["prompt_kind"] == "pending_input"
    assert "Which Airtel line should I buy it for?" in prompt["text"]
    assert "08162511023" not in prompt["text"]
    assert "Review Airtime Purchase" not in prompt["text"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_confirmation_show_other_options_reselects_from_catalog() -> None:
    worker = DataWorker(
        extractor=_EmptyDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="show_options",
            confidence=0.95,
            detected_language="English",
            target_types=["data"],
            show_options=True,
            reason="user asked for alternate data plans",
        )
    )
    config = _transaction_config(data=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_data_options",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="what other plan within that range",
        loaded_context=_banking_context(),
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["data_edit"]),
        tasks={
            "data_edit": TaskSpec(
                id="data_edit",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "buy_data",
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "phone": "08162511023",
                    "is_self": True,
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL104",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                    "confirmation": {
                        "summary": "Old data confirmation",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                        },
                    },
                },
            )
        },
        waves=[["data_edit"]],
        current_wave_index=0,
    )

    interrupt_updates = await handle_pending_interrupt(state, config)
    patched_payload = interrupt_updates["tasks"]["data_edit"].payload
    assert interrupt_updates["pending_interrupt"] is None
    assert interrupt_updates["tasks"]["data_edit"].stage == TaskStage.EXTRACTED
    assert patched_payload["plan_code"] is None
    assert patched_payload["show_plan_options"] is True
    assert patched_payload["data_plan_exclude_codes"] == ["MD501"]
    state = _apply_updates(state, interrupt_updates)

    option_updates = await advance_wave(state, config)
    assert option_updates["pending_interrupt"].kind == "input"
    assert option_updates["pending_interrupt"].fields_by_task == {"data_edit": ["data_plan_id"]}
    assert len(option_updates["outbox"]) == 1
    prompt = option_updates["outbox"][0]
    assert prompt["type"] == "say"
    assert "I found a few matching data plans:" in prompt["text"]
    assert "MTN 3.5 GB" in prompt["text"]
    assert "MTN 1.5 GB" in prompt["text"]
    assert "Reply with 1 or 2." in prompt["text"]
    assert "Reply with 1, 2, or 3." not in prompt["text"]
    assert "MTN 5 GB data bundle" not in prompt["text"]
    assert "Confirm Data Purchase" not in prompt["text"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_input_show_other_options_reselects_before_phone_prompt() -> None:
    worker = DataWorker(
        extractor=_EmptyDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="show_options",
            confidence=0.95,
            detected_language="English",
            target_types=["data"],
            show_options=True,
            reason="user asked for alternate data plans before choosing a line",
        )
    )
    config = _transaction_config(data=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_data_input_options",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="what other plan within that range",
        loaded_context=_banking_context(),
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["data_input"],
            fields_by_task={"data_input": ["target_phone"]},
            prompt="Which line should I buy it for?",
        ),
        tasks={
            "data_input": TaskSpec(
                id="data_input",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={
                    "action": "buy_data",
                    "amount": 3500,
                    "network": "MTN",
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL104",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                },
            )
        },
        waves=[["data_input"]],
        current_wave_index=0,
    )

    interrupt_updates = await handle_pending_interrupt(state, config)
    patched_payload = interrupt_updates["tasks"]["data_input"].payload
    assert interrupt_updates["pending_interrupt"] is None
    assert interrupt_updates["tasks"]["data_input"].stage == TaskStage.EXTRACTED
    assert patched_payload["plan_code"] is None
    assert patched_payload["show_plan_options"] is True
    assert patched_payload["data_plan_exclude_codes"] == ["MD501"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0

    state = _apply_updates(state, interrupt_updates)
    option_updates = await advance_wave(state, config)

    assert option_updates["pending_interrupt"].kind == "input"
    assert option_updates["pending_interrupt"].fields_by_task == {"data_input": ["data_plan_id"]}
    assert len(option_updates["outbox"]) == 1
    prompt = option_updates["outbox"][0]
    assert prompt["type"] == "say"
    assert "I found a few matching data plans:" in prompt["text"]
    assert "MTN 3.5 GB" in prompt["text"]
    assert "MTN 5 GB data bundle" not in prompt["text"]
    assert "Which line" not in prompt["text"]
    assert "Confirm Data Purchase" not in prompt["text"]


@pytest.mark.asyncio
async def test_quality_audit_data_option_reply_after_alternatives_reconfirms_selected_plan() -> None:
    worker = DataWorker(
        extractor=_EmptyDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="show_options",
            confidence=0.95,
            detected_language="English",
            target_types=["data"],
            show_options=True,
            reason="user asked for alternate data plans before choosing a line",
        )
    )
    config = _transaction_config(data=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_data_option_after_options",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="what other plan within that range",
        loaded_context=_banking_context(),
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["data_option"],
            fields_by_task={"data_option": ["target_phone"]},
            prompt="Which line should I buy it for?",
        ),
        tasks={
            "data_option": TaskSpec(
                id="data_option",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={
                    "action": "buy_data",
                    "amount": 3500,
                    "network": "MTN",
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL104",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                },
            )
        },
        waves=[["data_option"]],
        current_wave_index=0,
    )

    state = _apply_updates(state, await handle_pending_interrupt(state, config))
    option_prompt_updates = await advance_wave(state, config)
    assert option_prompt_updates["pending_interrupt"].fields_by_task == {"data_option": ["data_plan_id"]}
    assert "MTN 1.5 GB" in option_prompt_updates["outbox"][0]["text"]

    state = _apply_updates(state, option_prompt_updates)
    state = _next_user_turn(state, "option 2")
    selection_updates = await handle_pending_interrupt(state, config)
    assert selection_updates["pending_interrupt"] is None
    state = _apply_updates(state, selection_updates)

    confirmation_updates = await advance_wave(state, config)

    assert confirmation_updates["pending_interrupt"].kind == "confirmation"
    assert len(confirmation_updates["outbox"]) == 1
    confirmation = confirmation_updates["outbox"][0]
    assert confirmation["type"] == "request_confirmation"
    assert confirmation["header"] == "Review Data Purchase"
    assert "MTN 1.5 GB for your number (08162511023)" in confirmation["summary"]
    assert "Amount: ₦1,000" in confirmation["summary"]
    assert "MTN 5 GB data bundle" not in confirmation["summary"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_confirmation_amount_edit_explains_reselected_plan() -> None:
    worker = DataWorker(
        extractor=_EmptyDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="update_fields",
            confidence=0.95,
            detected_language="English",
            target_types=["data"],
            amount=2000,
            reason="user changed data budget",
        )
    )
    config = _transaction_config(data=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_data_amount_edit",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="make it 2k",
        loaded_context=_banking_context(),
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["data_edit"]),
        tasks={
            "data_edit": TaskSpec(
                id="data_edit",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "buy_data",
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "is_self": True,
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL104",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                    "confirmation": {
                        "summary": "Old data confirmation",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "target_phone": "08162511023",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                        },
                    },
                },
            )
        },
        waves=[["data_edit"]],
        current_wave_index=0,
    )

    interrupt_updates = await handle_pending_interrupt(state, config)
    assert interrupt_updates["pending_interrupt"] is None
    state = _apply_updates(state, interrupt_updates)

    confirmation_updates = await advance_wave(state, config)

    assert confirmation_updates["pending_interrupt"].kind == "confirmation"
    assert confirmation_updates["outbox"][0] == {
        "type": "say",
        "text": "Got it, using MTN 3.5 GB at ₦2,000.",
    }
    confirmation = confirmation_updates["outbox"][1]
    assert confirmation["type"] == "request_confirmation"
    assert confirmation["header"] == "Review Data Purchase"
    assert "MTN 3.5 GB for your number (08162511023)" in confirmation["summary"]
    assert "Amount: ₦2,000" in confirmation["summary"]
    assert "MTN 5 GB data bundle" not in confirmation["summary"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_confirmation_source_edit_reconfirms_without_stale_source() -> None:
    worker = DataWorker(
        extractor=_EmptyDataExtractor(),
        bill_provider=_QualityDataBillProvider(),
        transaction_repo=None,
        publisher=None,
    )
    planner = _PendingEditAuditPlanner(
        PendingActionEditDecision(
            operation="update_fields",
            confidence=0.95,
            detected_language="English",
            target_types=["data"],
            source_bank_name="GTBank",
            reason="user changed data source account",
        )
    )
    config = _transaction_config(data=worker)
    config["configurable"]["task_planner"] = planner
    state = OrchestratorState(
        user_id="u_quality_data_source_edit",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="use my GTBank",
        loaded_context=_banking_context_with_gtbank(),
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["data_source_edit"]),
        tasks={
            "data_source_edit": TaskSpec(
                id="data_source_edit",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "buy_data",
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "is_self": True,
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL104",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                    "confirmation": {
                        "summary": "Old data confirmation",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "target_phone": "08162511023",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                            "sourceBank": "Access Bank",
                            "sourceAccount": "2010000003",
                        },
                    },
                },
            )
        },
        waves=[["data_source_edit"]],
        current_wave_index=0,
    )

    interrupt_updates = await handle_pending_interrupt(state, config)
    patched_payload = interrupt_updates["tasks"]["data_source_edit"].payload
    assert interrupt_updates["pending_interrupt"] is None
    assert interrupt_updates["tasks"]["data_source_edit"].stage == TaskStage.EXTRACTED
    assert patched_payload["source_account_id"] == "acc_gtb"
    assert patched_payload["source_bank_name"] == "GTBank"
    assert patched_payload["source_account_number"] == "2010000002"
    assert patched_payload["confirmation"] == {"confirmed": False}
    assert patched_payload["previous_confirmation_snapshot"]["sourceBank"] == "Access Bank"
    state = _apply_updates(state, interrupt_updates)

    confirmation_updates = await advance_wave(state, config)

    assert confirmation_updates["pending_interrupt"].kind == "confirmation"
    assert confirmation_updates["outbox"][0] == {"type": "say", "text": "Updated the source account to GTBank."}
    confirmation = confirmation_updates["outbox"][-1]
    assert confirmation["type"] == "request_confirmation"
    assert confirmation["header"] == "Review Data Purchase"
    assert "From: GTBank (···0002)" in confirmation["summary"]
    assert "From: Access Bank (···0003)" not in confirmation["summary"]
    assert "MTN 5 GB data bundle for your number (08162511023)" in confirmation["summary"]
    assert planner.edit_calls == 1
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_confirmation_balance_detour_stashes_and_runs_account() -> None:
    account_worker = _QualityAccountBalanceWorker()
    config = _transaction_config(account=account_worker)
    state = OrchestratorState(
        user_id="u_quality_data_balance_detour",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="Whats my access balance",
        loaded_context=_banking_context_with_gtbank(),
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["data_balance_detour"]),
        tasks={
            "data_balance_detour": TaskSpec(
                id="data_balance_detour",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "action": "buy_data",
                    "amount": 3500,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "is_self": True,
                    "plan_code": "MD501",
                    "plan_name": "MTN 5 GB data bundle",
                    "biller_code": "BIL104",
                    "plan_size_gb": 5.0,
                    "plan_validity_days": 30,
                    "source_account_id": "acc_access",
                    "source_bank_name": "Access Bank",
                    "source_account_name": "Gaines",
                    "source_account_number": "2010000003",
                    "confirmation": {
                        "summary": "Confirm Data Purchase\n\nMTN 5 GB data bundle for your number",
                        "snapshot": {
                            "amount": 3500,
                            "network": "MTN",
                            "target_phone": "08162511023",
                            "plan_code": "MD501",
                            "plan_name": "MTN 5 GB data bundle",
                        },
                    },
                },
            )
        },
        waves=[["data_balance_detour"]],
        current_wave_index=0,
        session_stack=[ActiveSession(domain="data", state="WAITING_FOR_INPUT", interrupt_policy="CONFIRM")],
        active_domain="data",
    )

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    assert len(interrupt_updates["stashed_sessions"]) == 1
    assert interrupt_updates["stashed_sessions"][0]["intent"] == "data"
    assert interrupt_updates["outbox"] == [
        {"type": "say", "text": "I paused the data purchase while I check your account."}
    ]
    account_task = next(iter(interrupt_updates["tasks"].values()))
    assert account_task.type == "account"
    assert account_task.payload["message"] == "Whats my access balance"

    switched_state = _apply_updates(state, interrupt_updates)
    account_updates = await advance_wave(switched_state, config)

    assert account_worker.last_user_message == "Whats my access balance"
    assert len(account_updates["outbox"]) == 1
    assert account_updates["outbox"][0] == {
        "type": "say",
        "text": "I paused the data purchase while I check your account.\n\nYour Access Bank account (···0003) has a balance of ₦30,000.00.",
    }
    assert switched_state.referent_memory.items
    assert any(item.source == "stashed_session" for item in switched_state.referent_memory.items)


@pytest.mark.asyncio
async def test_quality_audit_transfer_start_is_deterministic_and_bypasses_router() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_transfer_start_fastpath",
            initial_state=_state(user_id="u_audit_transfer"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="send 5k to Ada",
                    expect_path_shape="deterministic_transfer_domain",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="fresh_transfer_command",
                    expect_task_types=("transfer",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "transfer"
    assert task.payload["message"] == "send 5k to Ada"
    assert task.payload["instruction"] == "send 5k to Ada"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_data_start_is_deterministic_and_bypasses_router() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_data_start_fastpath",
            initial_state=_state(user_id="u_audit_data"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="buy 1gb data for me",
                    expect_path_shape="deterministic_data_domain",
                    expect_routing_owner="guardrail",
                    expect_task_types=("data",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "data"
    assert task.payload["message"] == "buy 1gb data for me"
    assert task.payload["instruction"] == "buy 1gb data for me"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_bare_data_start_is_deterministic_and_bypasses_router() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_bare_data_start_fastpath",
            initial_state=_state(user_id="u_audit_bare_data"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="I want to buy data",
                    expect_path_shape="deterministic_data_domain",
                    expect_routing_owner="guardrail",
                    expect_task_types=("data",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "data"
    assert task.payload["message"] == "I want to buy data"
    assert task.payload["instruction"] == "I want to buy data"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_airtime_start_is_deterministic_and_bypasses_router() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_airtime_start_fastpath",
            initial_state=_state(user_id="u_audit_airtime"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="buy me 1k airtime",
                    expect_path_shape="deterministic_airtime_domain",
                    expect_routing_owner="guardrail",
                    expect_task_types=("airtime",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "airtime"
    assert task.payload["message"] == "buy me 1k airtime"
    assert task.payload["instruction"] == "buy me 1k airtime"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_mixed_transfer_and_crypto_keeps_transfer_with_policy_notice() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_mixed_transfer_unsupported",
            initial_state=_state(user_id="u_audit_mixed_transfer"),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="send 5k to Ada and buy bitcoin for me",
                    expect_path_shape="mixed_capability_supported_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="mixed_supported_unsupported",
                    expect_task_types=("transfer",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert result.final_state.routing_target_domain == "transfer"
    assert result.final_state.policy_notice is not None
    assert "money transfer" in result.final_state.policy_notice
    assert "investments or crypto" in result.final_state.policy_notice
    assert result.final_state.capability_boundary is None
    assert task.payload["message"] == "send 5k to Ada"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_unsupported_followup_does_not_reuse_stale_transaction_context() -> None:
    planner = _AuditPlanner(
        boundary_decision=UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="lending",
            confidence=0.91,
            reason="repayment promise continues lending request",
        )
    )

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_unsupported_boundary_no_stale_context",
            initial_state=_state(
                user_id="u_audit_unsupported",
                context_frames=[_stale_transfer_frame()],
            ),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="Can you borrow me money?",
                    expect_response_contains=("cannot assist with loans",),
                    expect_response_not_contains=("Tolu",),
                    expect_path_shape="meta_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="meta_direct",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
                ConversationTurn(
                    user="I will pay back",
                    expect_response_contains=("cannot help with loans",),
                    expect_response_not_contains=("Tolu", "transfer to"),
                    expect_path_shape="capability_boundary_followup",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="unsupported_capability_followup",
                    expect_task_types=(),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert planner.boundary_calls == 0
    assert planner.route_calls == 0
    assert result.final_state.capability_boundary is not None
    assert result.final_state.capability_boundary.key == "lending"
    assert result.final_state.capability_boundary.followup_count == 1


@pytest.mark.asyncio
async def test_quality_audit_supported_data_request_clears_unsupported_boundary() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_supported_breakout_from_boundary",
            initial_state=_state(
                user_id="u_audit_boundary_breakout",
                capability_boundary=CapabilityBoundary(key="investments", label="investments or crypto"),
            ),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="buy 1gb data for me",
                    expect_path_shape="deterministic_data_domain",
                    expect_routing_owner="guardrail",
                    expect_task_types=("data",),
                    expect_planner_route_calls_delta=0,
                ),
            ),
        )
    )

    assert result.final_state.capability_boundary is None
    assert result.final_state.routing_target_domain == "data"
    assert planner.route_calls == 0


@pytest.mark.asyncio
async def test_quality_audit_resume_prompt_accepts_polite_approval() -> None:
    planner = _AuditPlanner()

    result = await run_conversation_scenario(
        ConversationScenario(
            id="quality_resume_polite_approval",
            initial_state=_state(
                user_id="u_audit_resume",
                context_frames=[_resume_prompt_frame()],
                stashed_sessions=[
                    {
                        "stash_id": "stash-audit",
                        "intent": "transfer",
                        "tasks": {},
                        "waves": [],
                        "current_wave_index": 0,
                    }
                ],
            ),
            planner=planner,
            turns=(
                ConversationTurn(
                    user="Yes please",
                    expect_path_shape="resume_session_direct",
                    expect_routing_owner="guardrail",
                    expect_routing_decision="resume_session_direct",
                    expect_task_types=("orchestrator",),
                    expect_planner_route_calls_delta=0,
                    expect_state={"direct_path_triggered": True},
                ),
            ),
        )
    )

    task = next(iter(result.final_state.tasks.values()))
    assert task.payload == {"action": "resume_session"}
    assert planner.route_calls == 0
