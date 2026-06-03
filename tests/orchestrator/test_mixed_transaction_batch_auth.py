"""Regression tests for mixed transfer+airtime confirmation/auth batching."""

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize import finalize
from apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest import ingest_message
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from apps.chat.src.agent.workers.airtime.worker import AirtimeWorker
from banking.presentation.formatters.accounts import format_source_account_info_from_account_number
from banking.presentation.i18n.bridge import render_cancelled_prompt
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import (
    InterruptRouteDecision,
    PendingActionEditDecision,
    PlannedTask,
    PlannerOutput,
    SemanticRouteDecision,
    TaskParameters,
)

SHARED_SOURCE_LINE = format_source_account_info_from_account_number(
    bank="Zenith Bank",
    account_number="0000009384",
    locale="en",
    balance=None,
)


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text, context
        return self._output

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return PendingActionEditDecision(operation="unclear", confidence=0.0, reason="not an edit")


class _SequentialPlanner:
    def __init__(self, outputs: list[PlannerOutput]) -> None:
        self._outputs = outputs
        self._idx = 0

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text, context
        if not self._outputs:
            raise AssertionError("expected at least one planner output")
        if self._idx >= len(self._outputs):
            return self._outputs[-1]
        output = self._outputs[self._idx]
        self._idx += 1
        return output

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        del phone_number, context, path_label, prompt_mode
        if text.strip().lower() == "cancel":
            return InterruptRouteDecision(
                decision="cancel",
                confidence=0.99,
                detected_language="English",
                reason="explicit cancellation",
            )
        return InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.9,
            detected_language="English",
            reason="slot continuation",
        )

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return PendingActionEditDecision(operation="unclear", confidence=0.0, reason="not an edit")


class _PendingActionEditPlanner:
    def __init__(
        self,
        decision: PendingActionEditDecision,
        semantic_decision: SemanticRouteDecision | Any | None = None,
    ) -> None:
        self._decision = decision
        self._semantic_decision = semantic_decision
        self.semantic_contexts: list[str] = []

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        return self._decision

    async def route_semantic_turn(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> SemanticRouteDecision:
        del phone_number, text, path_label
        self.semantic_contexts.append(context)
        if callable(self._semantic_decision):
            return self._semantic_decision(context)
        if self._semantic_decision is not None:
            return self._semantic_decision
        if len(self._decision.target_types) == 1:
            target_type = str(self._decision.target_types[0])
            return SemanticRouteDecision(
                decision=f"domain_{target_type}",
                confidence=self._decision.confidence,
                target_intent=target_type,
                expected_transaction_executors=[target_type],
            )
        return SemanticRouteDecision(decision="planner_ambiguous", confidence=0.0)


class _PendingActionEditThenRoutePlanner(_PendingActionEditPlanner):
    def __init__(
        self,
        decision: PendingActionEditDecision,
        route: InterruptRouteDecision,
    ) -> None:
        super().__init__(decision)
        self._route = route
        self.route_calls = 0

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        del phone_number, text, context, path_label, prompt_mode
        self.route_calls += 1
        return self._route


class _SequentialPendingActionEditPlanner(_PendingActionEditPlanner):
    def __init__(self, decisions: list[PendingActionEditDecision]) -> None:
        if not decisions:
            raise ValueError("decisions must not be empty")
        super().__init__(decisions[0])
        self._decisions = list(decisions)
        self._index = 0

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        del phone_number, text, context, path_label
        decision = self._decisions[min(self._index, len(self._decisions) - 1)]
        self._index += 1
        return decision


class _BlankAirtimeExtractor:
    async def run(self, state: dict) -> dict:
        del state
        return {"entities": {}}


class _NetworkFollowupAirtimeExtractor:
    async def run(self, state: dict) -> dict:
        message = str(state.get("message") or "").lower()
        if "mtn" in message:
            return {"entities": {"network": "mtn"}}
        return {"entities": {}}


class _ModelDumpObject:
    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self, exclude_none: bool = False) -> dict:
        if exclude_none:
            return {key: value for key, value in self._data.items() if value is not None}
        return dict(self._data)


class _ExtractionObject:
    def __init__(self, *, entities: dict) -> None:
        self.entities = _ModelDumpObject(entities)
        self.correction = None
        self.requested_features = []
        self.acknowledgment = ""


class _InterruptAirtimeExtractor:
    async def extract(self, text: str, smart_context: dict | None = None) -> object:
        del text, smart_context
        return _ExtractionObject(
            entities={
                "amount": 1000,
                "recipient_phone": "08162511023",
                "network": "mtn",
            }
        )


class _InterruptDataExtractor:
    async def extract(self, text: str, smart_context: dict | None = None) -> object:
        del text, smart_context
        return _ExtractionObject(
            entities={
                "budget": 1000,
                "recipient_phone": "08162511023",
                "network": "mtn",
                "size_preference": "1GB",
            }
        )


class _InterruptTransferExtractor:
    async def extract(self, text: str, smart_context: dict | None = None) -> object:
        del text, smart_context
        return _ExtractionObject(
            entities={
                "amount": 2000,
                "recipient_name": "Tolu",
                "recipient_account": "2010000001",
                "bank_name": "Access Bank",
            }
        )


class _AmountOnlyInterruptAirtimeExtractor:
    async def extract(self, text: str, smart_context: dict | None = None) -> object:
        del text, smart_context
        return _ExtractionObject(entities={"amount": 1000})


class _TransferNeedsConfirmationWorker:
    extractor = _InterruptTransferExtractor()

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm transfer task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_name": payload.get("recipient_name"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _TransferNeedsConfirmationWorkerWithUpdate:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm transfer task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_name": payload.get("recipient_name"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
            update_message=payload.get("update_message_override"),
        )


class _TransferExecutesWhenPinVerifiedWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message
        if pin_verified:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt={
                    "status": "processing",
                    "amount": payload.get("amount", 0),
                    "recipient_name": payload.get("recipient_resolved_name") or payload.get("recipient_name"),
                },
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm transfer task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_name": payload.get("recipient_name"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _AirtimeNeedsConfirmationWorker:
    extractor = _InterruptAirtimeExtractor()

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm airtime task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_phone": payload.get("recipient_phone"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _DataNeedsConfirmationWorker:
    extractor = _InterruptDataExtractor()

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm data task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "target_phone": payload.get("target_phone"),
                "network": payload.get("network"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _AmountOnlyAirtimeNeedsConfirmationWorker(_AirtimeNeedsConfirmationWorker):
    extractor = _AmountOnlyInterruptAirtimeExtractor()


class _AirtimeExecutesWhenPinVerifiedWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message
        if pin_verified:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt={
                    "status": "processing",
                    "amount": payload.get("amount", 0),
                    "recipient_phone": payload.get("recipient_phone"),
                    "network": payload.get("network"),
                },
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="Confirm airtime task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_phone": payload.get("recipient_phone"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _AirtimeCorrectionNeedsConfirmationWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, pin_verified
        amount = payload.get("amount", 0)
        if user_message and "2k" in user_message.lower():
            amount = 2000
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={"amount": amount},
            confirmation_summary="Confirm airtime task",
            confirmation_snapshot={
                "amount": amount,
                "recipient_phone": payload.get("recipient_phone"),
                "network": payload.get("network"),
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _TransferNeedsInputWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt="Need transfer details.",
        )


class _TransferResolvedSiblingAndMissingFocusedWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        recipient = str(payload.get("recipient_name") or "").lower()
        if recipient == "gaines":
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt="Need transfer details.",
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={
                "recipient_name": "Tolu Adebayo",
                "recipient_resolved_name": "Tolu Adebayo",
                "recipient_account": "2010000001",
                "recipient_bank_name": "Access Bank",
                "source_account_id": "acct-1",
            },
            confirmation_summary="Confirm transfer task",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_name": "Tolu Adebayo",
                "sourceBank": "Zenith Bank",
                "sourceAccount": "0000009384",
            },
        )


class _TransferTwoRecipientsOneNeedsDetailsWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del pin_verified
        recipient = str(payload.get("recipient_name") or "").lower()
        required_fields = set(context.get("required_fields") or [])
        if recipient == "gaines":
            if required_fields == {"recipient_account", "recipient_bank_name"}:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["source_account_id"],
                    prompt="I found Gaines (FATIMA ZAHRA MUSA).\n\nWhich account would you like to use?",
                    patch={
                        "recipient_name": "Gaines",
                        "recipient_resolved_name": "FATIMA ZAHRA MUSA",
                        "recipient_account": "0760705267",
                        "recipient_bank_name": "Access",
                        "source_account_id": None,
                    },
                )
            if required_fields == {"source_account_id"} or payload.get("source_account_id"):
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                    confirmation_summary="₦10,000 → Gaines (Fatima Zahra Musa)\nAccess • 0760705267",
                    confirmation_snapshot={
                        "amount": payload.get("amount", 0),
                        "recipient_name": "Gaines (FATIMA ZAHRA MUSA)",
                        "recipient_bank": "Access",
                        "recipient_account": "0760705267",
                        "sourceBank": "First Bank",
                        "sourceAccount": "6000000001",
                    },
                    patch={"source_account_id": "acct-1"},
                )
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt="Need transfer details.",
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="₦5,000 → Tolu Adebayo\nAccess Bank • 2010000001",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_name": "Tolu Adebayo",
                "recipient_bank": "Access Bank",
                "recipient_account": "2010000001",
                "sourceBank": "First Bank",
                "sourceAccount": "6000000001",
            },
            patch={
                "recipient_name": "Tolu Adebayo",
                "recipient_resolved_name": "Tolu Adebayo",
                "recipient_account": "2010000001",
                "recipient_bank_name": "Access Bank",
                "source_account_id": "acct-1",
            },
        )


class _TransferSourceSelectionWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary="₦10,000 → Gaines (Fatima Zahra Musa)\nAccess • 0760705267",
            confirmation_snapshot={
                "amount": payload.get("amount", 0),
                "recipient_name": "Gaines (FATIMA ZAHRA MUSA)",
                "recipient_bank": "Access",
                "recipient_account": "0760705267",
                "sourceBank": "GTBank",
                "sourceAccount": "6000000002",
            },
            patch={
                "source_account_id": "acct-gtb",
                "source_bank_name": "GTBank",
                "source_account_name": "Gaines",
                "source_account_number": "6000000002",
                "source_affinity_mode": "explicit",
            },
        )


class _TransferBeneficiaryThenSourceThenConfirmationWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del pin_verified
        required_fields = set(context.get("required_fields") or [])
        if required_fields == {"beneficiary_id"}:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["source_account_id"],
                prompt="I found Tolu (Tolu Adebayo).\n\nWhich account would you like to use?",
                patch={
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": None,
                },
            )
        if required_fields == {"source_account_id"}:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                confirmation_summary="Confirm transfer task",
                confirmation_snapshot={
                    "amount": payload.get("amount", 0),
                    "recipient_name": payload.get("recipient_name"),
                    "sourceBank": "GTBank",
                    "sourceAccount": "0000000002",
                },
                patch={"source_account_id": "acct-2"},
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["beneficiary_id"],
            prompt="I found multiple matches for 'Tolu'. Which one did you mean?\nReply with the number or rephrase.",
            details={
                "options": [
                    {"id": "bene-1", "title": "Tolu Access (Tolu Adebayo)"},
                    {"id": "bene-2", "title": "Tolu GTB (Tolu Adeyemi)"},
                ]
            },
            patch={
                "beneficiary_candidates": [
                    {"id": "bene-1", "label": "Tolu Access (Tolu Adebayo)"},
                    {"id": "bene-2", "label": "Tolu GTB (Tolu Adeyemi)"},
                ],
            },
        )


class _AirtimeNeedsNetworkInputWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["network"],
            prompt="Need airtime network.",
        )


class _TransferFailWorker:
    def __init__(self) -> None:
        self.call_count = 0

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        self.call_count += 1
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error="transfer failed",
        )


class _AirtimeSuccessWorker:
    def __init__(self) -> None:
        self.call_count = 0

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        self.call_count += 1
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            receipt={"status": "success"},
        )


def _apply(state: OrchestratorState, updates: dict) -> OrchestratorState:
    return state.model_copy(update=updates)


def _base_state() -> OrchestratorState:
    return OrchestratorState(
        user_id="u_mixed_batch_auth",
        phone_number="2348000000900",
        channel="whatsapp",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )


def _planner_state() -> OrchestratorState:
    return OrchestratorState(
        user_id="u_mixed_batch_auth_conv",
        phone_number="2348000000900",
        channel="whatsapp",
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_expired_transaction_confirmation_interrupt_resets_session_and_replans_fresh_message() -> None:
    state = _base_state().model_copy(
        update={
            "last_message_text": "Hi",
            "pending_interrupt": PendingInterrupt(
                kind="confirmation",
                task_ids=["t_transfer", "t_airtime"],
                created_at_ts=1.0,
            ),
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={"amount": 10000},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={"amount": 1000},
                ),
            },
        }
    )
    planner = _MockPlanner(
        PlannerOutput(
            primary_intent="conversational",
            response="",
            response_key="conversational.greeting",
            confidence=0.9,
            detected_language="English",
            tasks=[],
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert "final_response" not in updates
    assert "outbox" not in updates
    assert updates["last_interrupt"].kind == "confirmation"

    state = _apply(state, updates)
    plan_updates = await plan_tasks(state, config)

    assert plan_updates["final_response"] == render_message("conversational.greeting", "en")


@pytest.mark.asyncio
async def test_expired_transaction_confirmation_continuation_gets_standard_response() -> None:
    state = _base_state().model_copy(
        update={
            "last_message_text": "Yes",
            "pending_interrupt": PendingInterrupt(
                kind="confirmation",
                task_ids=["t_transfer", "t_airtime"],
                created_at_ts=1.0,
            ),
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={"amount": 10000},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={"amount": 1000},
                ),
            },
        }
    )
    planner = _MockPlanner(
        PlannerOutput(
            primary_intent="conversational",
            response="",
            response_key="conversational.greeting",
            confidence=0.9,
            detected_language="English",
            tasks=[],
        )
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"] == {}
    assert updates["waves"] == []
    assert updates["final_response"] == (
        "That transaction session has expired, so I can't continue it. Please start the transaction again."
    )
    assert updates["semantic_path_shape"] == "expired_transaction_session"
    assert updates["outbox"] == [{"type": "say", "text": updates["final_response"]}]
    assert updates["last_interrupt"].kind == "confirmation"


@pytest.mark.asyncio
async def test_mixed_transfer_airtime_uses_single_confirmation_and_single_auth_gate() -> None:
    state = _base_state().model_copy(
        update={
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 10000, "recipient_name": "Mum", "source_account_id": "acct-1"},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 5000,
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-1",
                    },
                ),
            }
        }
    )
    config: RunnableConfig = {
        "configurable": {
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            }
        },
        "recursion_limit": 50,
    }

    first_updates = await advance_wave(state, config)
    confirmation_entry = next(entry for entry in first_updates["outbox"] if entry["type"] == "request_confirmation")
    assert first_updates["pending_interrupt"].kind == "confirmation"
    assert set(first_updates["pending_interrupt"].task_ids) == {"t_transfer", "t_airtime"}
    assert set(confirmation_entry["task_ids"]) == {"t_transfer", "t_airtime"}
    assert "*Transfer*" in confirmation_entry["summary"]
    assert "*Airtime*" in confirmation_entry["summary"]
    assert "Confirm transfer task" in confirmation_entry["summary"]
    assert "Confirm airtime task" in confirmation_entry["summary"]
    assert confirmation_entry["summary"].count(SHARED_SOURCE_LINE) == 1
    assert set(confirmation_entry["snapshots_by_task"].keys()) == {"t_transfer", "t_airtime"}

    post_confirm_state = state.model_copy(update=first_updates)
    post_confirm_state.pending_interrupt = first_updates["pending_interrupt"]
    post_confirm_state.last_message_text = "yes"

    confirm_updates = await handle_pending_interrupt(post_confirm_state, config)
    ready_for_auth_state = post_confirm_state.model_copy(update=confirm_updates)

    auth_updates = await advance_wave(ready_for_auth_state, config)
    auth_entry = next(entry for entry in auth_updates["outbox"] if entry["type"] == "auth_request")
    assert auth_updates["pending_interrupt"].kind == "auth"
    assert set(auth_updates["pending_interrupt"].task_ids) == {"t_transfer", "t_airtime"}
    assert set(auth_entry["task_ids"]) == {"t_transfer", "t_airtime"}
    assert auth_entry["header"] == "Authorize Transaction"
    assert "*Transfer*" in auth_entry["summary"]
    assert "*Airtime*" in auth_entry["summary"]
    assert "Confirm transfer task" in auth_entry["summary"]
    assert "Confirm airtime task" in auth_entry["summary"]
    assert auth_entry["summary"].count(SHARED_SOURCE_LINE) == 1


@pytest.mark.asyncio
async def test_single_transfer_update_message_precedes_confirmation_prompt() -> None:
    state = OrchestratorState(
        user_id="u_transfer_update_msg",
        phone_number="2348000000900",
        channel="whatsapp",
        waves=[["t_transfer"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 20000,
                    "recipient_name": "Mum",
                    "source_account_id": "acct-1",
                    "update_message_override": "Changing amount to ₦20,000.",
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsConfirmationWorkerWithUpdate()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    assert updates["outbox"][0] == {"type": "say", "text": "Changing amount to ₦20,000."}
    assert updates["outbox"][1]["type"] == "request_confirmation"


@pytest.mark.asyncio
async def test_transfer_confirmation_clears_transient_transition_metadata() -> None:
    state = OrchestratorState(
        user_id="u_transfer_update_cleanup",
        phone_number="2348000000900",
        channel="whatsapp",
        waves=[["t_transfer"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 20000,
                    "recipient_name": "Mum",
                    "source_account_id": "acct-1",
                    "transition_acknowledgment": "Changing amount to ₦20,000.",
                    "previous_confirmation_snapshot": {"amount": 10000, "recipient_name": "Mum"},
                    "update_message_override": "Changing amount to ₦20,000.",
                },
            )
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsConfirmationWorkerWithUpdate()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    transfer_payload = updates["tasks"]["t_transfer"].payload
    assert "transition_acknowledgment" not in transfer_payload
    assert "previous_confirmation_snapshot" not in transfer_payload


@pytest.mark.asyncio
async def test_multi_transfer_update_messages_compact_to_single_heads_up() -> None:
    state = OrchestratorState(
        user_id="u_transfer_update_batch",
        phone_number="2348000000900",
        channel="whatsapp",
        waves=[["t1", "t2"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 20000,
                    "recipient_name": "Mum",
                    "source_account_id": "acct-1",
                    "update_message_override": "Changing amount to ₦20,000.",
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "source_account_id": "acct-1",
                    "update_message_override": "Updating recipient to Gaines.",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsConfirmationWorkerWithUpdate()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    say_entries = [entry for entry in updates["outbox"] if entry.get("type") == "say"]
    assert len(say_entries) == 1
    assert "changing your transfer details" in say_entries[0]["text"].lower()
    assert any(entry.get("type") == "request_confirmation" for entry in updates["outbox"])


@pytest.mark.asyncio
async def test_mixed_input_prompt_includes_queued_next_notice_for_sibling_transaction() -> None:
    state = _base_state().model_copy(
        update={
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 10000, "recipient_name": "Mum", "source_account_id": "acct-1"},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={
                        "amount": 5000,
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-1",
                        "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 5000}},
                    },
                ),
            }
        }
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferNeedsInputWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)
    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].task_ids == ["t_transfer"]

    say_entry = updates["outbox"][0]
    assert say_entry["type"] == "say"
    assert "Also in this batch" in say_entry["text"]
    assert "Buy ₦5,000 airtime for 08162511023" in say_entry["text"]
    assert say_entry["queue"]["queued_task_ids"] == ["t_airtime"]


@pytest.mark.asyncio
async def test_mixed_input_prompt_does_not_treat_airtime_target_as_found_recipient() -> None:
    state = _base_state().model_copy(
        update={
            "waves": [["t_gaines", "t_tolu", "t_airtime"]],
            "tasks": {
                "t_gaines": TaskSpec(
                    id="t_gaines",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 10000, "recipient_name": "Gaines", "source_account_id": "acct-1"},
                ),
                "t_tolu": TaskSpec(
                    id="t_tolu",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 5000, "recipient_name": "Tolu Adebayo", "source_account_id": "acct-1"},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 1000,
                        "recipient_name": "My Number",
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-1",
                    },
                ),
            },
        }
    )
    config: RunnableConfig = {
        "configurable": {
            "services": {
                "transfer": _TransferResolvedSiblingAndMissingFocusedWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            }
        },
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    prompt = updates["outbox"][0]["text"]
    assert "I found Tolu Adebayo. I'll also buy ₦1,000 airtime for 08162511023." in prompt
    assert "My Number" not in prompt
    assert "I still need Gaines' account number and bank." in prompt
    assert "Buy ₦1,000 airtime for 08162511023" in prompt


@pytest.mark.asyncio
async def test_mixed_two_transfers_and_airtime_keep_all_tasks_after_late_source_selection() -> None:
    state = _base_state().model_copy(
        update={
            "waves": [["t_gaines", "t_tolu", "t_airtime"]],
            "tasks": {
                "t_gaines": TaskSpec(
                    id="t_gaines",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 10000, "recipient_name": "Gaines"},
                ),
                "t_tolu": TaskSpec(
                    id="t_tolu",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 5000, "recipient_name": "Tolu Adebayo"},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 1000,
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-1",
                    },
                ),
            },
        }
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _SequentialPlanner([]),
            "services": {
                "transfer": _TransferTwoRecipientsOneNeedsDetailsWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    first_updates = await advance_wave(state, config)
    state = _apply(state, first_updates).model_copy(
        update={
            "last_message_text": "0760705267, Access",
            "waves": [["t_gaines", "t_airtime"]],
        }
    )

    details_updates = await handle_pending_interrupt(state, config)
    state = _apply(state, details_updates)
    source_updates = await advance_wave(state, config)
    state = _apply(state, source_updates).model_copy(update={"last_message_text": "1"})

    source_selection_updates = await handle_pending_interrupt(state, config)
    state = _apply(state, source_selection_updates)
    final_updates = await advance_wave(state, config)

    confirmation = next(entry for entry in final_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(final_updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu", "t_airtime"}
    assert set(confirmation["task_ids"]) == {"t_gaines", "t_tolu", "t_airtime"}
    assert "Gaines" in confirmation["summary"]
    assert "Tolu Adebayo" in confirmation["summary"]
    assert set(confirmation["snapshots_by_task"]) == {"t_gaines", "t_tolu", "t_airtime"}


@pytest.mark.asyncio
async def test_batch_source_selection_applies_to_transfer_and_airtime_siblings() -> None:
    state = _base_state().model_copy(
        update={
            "last_message_text": "Gtb",
            "last_interrupt": PendingInterrupt(
                kind="input",
                task_ids=["t_gaines"],
                fields_by_task={"t_gaines": ["source_account_id"]},
                prompt="Which account would you like to use?",
            ),
            "waves": [["t_gaines", "t_airtime"]],
            "loaded_context": {
                "language": "en",
                "accounts": [
                    {
                        "id": "acct-first",
                        "bank_name": "First Bank",
                        "account_number": "6000000001",
                        "mandate_status": "ready",
                        "mandate_id": "m1",
                    },
                    {
                        "id": "acct-gtb",
                        "bank_name": "GTBank",
                        "account_number": "6000000002",
                        "mandate_status": "ready",
                        "mandate_id": "m2",
                    },
                    {
                        "id": "acct-access",
                        "bank_name": "Access Bank",
                        "account_number": "6000000003",
                        "mandate_status": "ready",
                        "mandate_id": "m3",
                    },
                ],
            },
            "tasks": {
                "t_gaines": TaskSpec(
                    id="t_gaines",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 10000,
                        "recipient_name": "Gaines",
                        "recipient_resolved_name": "FATIMA ZAHRA MUSA",
                        "recipient_account": "0760705267",
                        "recipient_bank_name": "Access",
                        "async_group_id": "batch-1",
                    },
                ),
                "t_tolu": TaskSpec(
                    id="t_tolu",
                    type="transfer",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={
                        "amount": 5000,
                        "recipient_name": "Tolu Adebayo",
                        "recipient_resolved_name": "Tolu Adebayo",
                        "recipient_account": "2010000001",
                        "recipient_bank_name": "Access Bank",
                        "source_account_id": "acct-access",
                        "source_bank_name": "Access Bank",
                        "source_account_number": "6000000003",
                        "source_affinity_mode": "auto",
                        "async_group_id": "batch-1",
                        "confirmation": {
                            "summary": "₦5,000 → Tolu Adebayo\nAccess Bank • 2010000001",
                            "snapshot": {
                                "amount": 5000,
                                "recipient_name": "Tolu Adebayo",
                                "recipient_bank": "Access Bank",
                                "recipient_account": "2010000001",
                                "sourceBank": "Access Bank",
                                "sourceAccount": "6000000003",
                            },
                        },
                    },
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.AWAITING_CONFIRMATION,
                    payload={
                        "amount": 1000,
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-access",
                        "source_bank_name": "Access Bank",
                        "source_account_number": "6000000003",
                        "source_affinity_mode": "auto",
                        "async_group_id": "batch-1",
                        "confirmation": {
                            "summary": "₦1,000 Airtime → 08162511023\nNetwork: MTN",
                            "snapshot": {
                                "amount": 1000,
                                "recipient_phone": "08162511023",
                                "network": "mtn",
                                "sourceBank": "Access Bank",
                                "sourceAccount": "6000000003",
                            },
                        },
                    },
                ),
            },
        }
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": _TransferSourceSelectionWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    prompt_text = "\n".join(str(entry.get("text") or entry.get("title") or "") for entry in updates["outbox"])
    assert "Which account would you like to use?" not in prompt_text
    confirmation = next(entry for entry in updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation["task_ids"]) == {"t_gaines", "t_tolu", "t_airtime"}
    assert set(updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu", "t_airtime"}
    assert "Tolu Adebayo" in confirmation["summary"]
    assert state.tasks["t_tolu"].payload["source_account_id"] == "acct-gtb"
    assert state.tasks["t_airtime"].payload["source_account_id"] == "acct-gtb"
    assert state.tasks["t_tolu"].payload["confirmation"]["snapshot"]["sourceBank"] == "GTBank"
    assert state.tasks["t_airtime"].payload["confirmation"]["snapshot"]["sourceBank"] == "GTBank"
    assert confirmation["snapshots_by_task"]["t_tolu"]["sourceBank"] == "GTBank"
    assert confirmation["snapshots_by_task"]["t_airtime"]["sourceBank"] == "GTBank"


@pytest.mark.asyncio
async def test_batch_source_selection_continues_extracted_same_batch_sibling() -> None:
    state = _base_state().model_copy(
        update={
            "last_message_text": "2",
            "last_interrupt": PendingInterrupt(
                kind="input",
                task_ids=["t_gaines", "t_airtime"],
                fields_by_task={"t_gaines": ["source_account_id"], "t_airtime": ["source_account_id"]},
                prompt="Which account would you like to use?",
            ),
            "waves": [["t_gaines", "t_tolu", "t_airtime"]],
            "tasks": {
                "t_gaines": TaskSpec(
                    id="t_gaines",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 10000,
                        "recipient_name": "Gaines",
                        "recipient_resolved_name": "FATIMA ZAHRA MUSA",
                        "recipient_account": "0760705267",
                        "recipient_bank_name": "Access",
                        "async_group_id": "batch-1",
                    },
                ),
                "t_tolu": TaskSpec(
                    id="t_tolu",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 5000,
                        "recipient_name": "Tolu Adebayo",
                        "recipient_resolved_name": "Tolu Adebayo",
                        "recipient_account": "2010000001",
                        "recipient_bank_name": "Access Bank",
                        "async_group_id": "batch-1",
                    },
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 1000,
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "async_group_id": "batch-1",
                    },
                ),
            },
        }
    )
    config: RunnableConfig = {
        "configurable": {
            "services": {
                "transfer": _TransferTwoRecipientsOneNeedsDetailsWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            }
        },
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    confirmation = next(entry for entry in updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu", "t_airtime"}
    assert set(confirmation["task_ids"]) == {"t_gaines", "t_tolu", "t_airtime"}
    assert updates["tasks"]["t_tolu"].stage == TaskStage.AWAITING_CONFIRMATION
    assert "Tolu Adebayo" in confirmation["summary"]


@pytest.mark.asyncio
async def test_mixed_transfer_clarification_keeps_airtime_in_final_confirmation() -> None:
    state = _base_state().model_copy(
        update={
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.EXTRACTED,
                    payload={"action": "send_money", "amount": 5000, "recipient_name": "Tolu"},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXTRACTED,
                    payload={
                        "amount": 500,
                        "action": "buy_airtime",
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-1",
                    },
                ),
            }
        }
    )
    config: RunnableConfig = {
        "configurable": {
            "services": {
                "transfer": _TransferBeneficiaryThenSourceThenConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            }
        },
        "recursion_limit": 50,
    }

    first_updates = await advance_wave(state, config)
    first_prompt = first_updates["outbox"][0]
    assert first_updates["pending_interrupt"].task_ids == ["t_transfer"]
    assert "Also in this batch" in (first_prompt.get("title") or first_prompt.get("text") or "")
    assert "Buy ₦500 airtime for 08162511023" in (first_prompt.get("title") or first_prompt.get("text") or "")
    assert first_prompt["queue"]["queued_task_ids"] == ["t_airtime"]

    state = _apply(state, first_updates).model_copy(update={"last_message_text": "1"})
    beneficiary_updates = await handle_pending_interrupt(state, config)
    state = _apply(state, beneficiary_updates)
    source_updates = await advance_wave(state, config)
    source_prompt = source_updates["outbox"][0]
    assert source_updates["pending_interrupt"].task_ids == ["t_transfer"]
    assert "Also in this batch" in (source_prompt.get("title") or source_prompt.get("text") or "")
    assert "Buy ₦500 airtime for 08162511023" in (source_prompt.get("title") or source_prompt.get("text") or "")
    assert source_prompt["queue"]["queued_task_ids"] == ["t_airtime"]

    state = _apply(state, source_updates).model_copy(update={"last_message_text": "1"})
    source_selection_updates = await handle_pending_interrupt(state, config)
    state = _apply(state, source_selection_updates)
    final_updates = await advance_wave(state, config)

    confirmation = next(entry for entry in final_updates["outbox"] if entry["type"] == "request_confirmation")
    assert final_updates["pending_interrupt"].kind == "confirmation"
    assert set(final_updates["pending_interrupt"].task_ids) == {"t_transfer", "t_airtime"}
    assert set(confirmation["task_ids"]) == {"t_transfer", "t_airtime"}
    assert confirmation["header"] == "Confirm Transactions"
    assert "Confirm transfer task" in confirmation["summary"]
    assert "Confirm airtime task" in confirmation["summary"]
    actionable_payload = confirmation["actionable_payload"]
    assert actionable_payload["task_type"] == "batch"
    assert {item["task_type"] for item in actionable_payload["tasks"]} == {"transfer", "airtime"}
    airtime_payload = next(item for item in actionable_payload["tasks"] if item["task_type"] == "airtime")
    assert airtime_payload["recipient_phone"] == "08162511023"


@pytest.mark.asyncio
async def test_mixed_auth_is_deferred_until_sibling_slots_are_collected() -> None:
    state = _base_state().model_copy(
        update={
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.AWAITING_AUTH,
                    payload={
                        "amount": 10000,
                        "recipient_name": "Mum",
                        "source_account_id": "acct-1",
                        "confirmation": {"summary": "Confirm transfer task", "snapshot": {"amount": 10000}},
                    },
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXTRACTED,
                    payload={"amount": 5000, "source_account_id": "acct-1"},
                ),
            }
        }
    )
    config: RunnableConfig = {
        "configurable": {"services": {"airtime": _AirtimeNeedsNetworkInputWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].task_ids == ["t_airtime"]
    assert "auth_request" not in {entry.get("type") for entry in updates.get("outbox", [])}
    say_entry = updates["outbox"][0]
    assert say_entry["type"] == "say"
    assert "Also in this batch" in say_entry["text"]
    assert "Send ₦10,000 to Mum" in say_entry["text"]
    assert say_entry["queue"]["queued_task_ids"] == ["t_transfer"]


@pytest.mark.asyncio
async def test_mixed_authorized_wave_continues_when_first_task_fails() -> None:
    transfer_worker = _TransferFailWorker()
    airtime_worker = _AirtimeSuccessWorker()
    state = _base_state().model_copy(
        update={
            "tasks": {
                "t_transfer": TaskSpec(
                    id="t_transfer",
                    type="transfer",
                    stage=TaskStage.EXECUTING,
                    payload={"amount": 10000, "recipient_name": "Mum", "source_account_id": "acct-1"},
                ),
                "t_airtime": TaskSpec(
                    id="t_airtime",
                    type="airtime",
                    stage=TaskStage.EXECUTING,
                    payload={
                        "amount": 5000,
                        "recipient_phone": "08162511023",
                        "network": "mtn",
                        "source_account_id": "acct-1",
                    },
                ),
            }
        }
    )
    config: RunnableConfig = {
        "configurable": {"services": {"transfer": transfer_worker, "airtime": airtime_worker}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)
    assert transfer_worker.call_count == 1
    assert airtime_worker.call_count == 1
    assert updates["tasks"]["t_transfer"].stage == TaskStage.FAILED
    assert updates["tasks"]["t_airtime"].stage == TaskStage.COMPLETED
    assert updates["current_wave_index"] == 1


@pytest.mark.asyncio
async def test_confirmation_correction_turn_reconfirms_without_completion_summary() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_reconfirm",
        phone_number="2348000000919",
        channel="whatsapp",
        last_message_text="Make the airtime 2k",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Yusuf Ibrahim",
                    "recipient_account": "0760505262",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm transfer task", "snapshot": {"amount": 10000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _SequentialPlanner([]),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeCorrectionNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)
    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_transfer", "t_airtime"}
    outbox = wave_updates["outbox"]
    assert len(outbox) == 1
    assert outbox[0]["type"] == "request_confirmation"
    assert "transaction summary" not in outbox[0]["summary"].lower()
    assert "all transactions completed successfully" not in outbox[0]["summary"].lower()


@pytest.mark.asyncio
async def test_confirmation_remove_airtime_drops_only_airtime_from_batch() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_remove_airtime",
        phone_number="2348000000920",
        channel="whatsapp",
        last_message_text="Remove airtime purchase",
        waves=[["t_gaines", "t_tolu", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Zenith Bank",
                    "account_number": "0000009384",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 3000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-1",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 3000}},
                },
            ),
        },
        task_results={
            "t_gaines": {"cached": "transfer"},
            "t_tolu": {"cached": "transfer"},
            "t_airtime": {"cached": "airtime"},
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="remove_tasks",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["airtime"],
                    target_texts=["airtime purchase"],
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    assert "t_airtime" not in interrupt_updates["tasks"]
    assert "t_airtime" not in interrupt_updates["task_results"]
    assert interrupt_updates["waves"] == [["t_gaines", "t_tolu"]]
    assert interrupt_updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_tolu"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_gaines"].payload["skip_extraction"] is True
    assert interrupt_updates["tasks"]["t_tolu"].payload["skip_extraction"] is True
    assert "t_airtime" in interrupt_updates["removed_confirmation_tasks"]

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu"}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_gaines", "t_tolu"}
    assert "airtime" not in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_can_remove_task_by_type() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_remove_airtime",
        phone_number="2348000000920",
        channel="whatsapp",
        last_message_text="abeg commot that recharge",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 3000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 3000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="remove_tasks",
                    confidence=0.91,
                    detected_language="Pidgin",
                    target_types=["airtime"],
                    target_texts=["recharge"],
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert set(updates["tasks"]) == {"t_transfer"}
    assert "t_airtime" in updates["removed_confirmation_tasks"]


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_applies_single_transfer_narration_without_target() -> None:
    state = OrchestratorState(
        user_id="u_single_confirm_semantic_narration",
        phone_number="2348000000927",
        channel="whatsapp",
        last_message_text="Add it's for my groceries",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-first",
                    "bank_name": "First Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-first",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.92,
                    detected_language="English",
                    narration="my groceries",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["last_interrupt"].task_ids == ["t_transfer"]
    assert updates["tasks"]["t_transfer"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_transfer"].payload["narration"] == "my groceries"
    assert updates["tasks"]["t_transfer"].payload["authored_narration"] == "my groceries"
    assert updates["tasks"]["t_transfer"].payload["user_note"] == "my groceries"
    assert "idempotency_key" not in updates["tasks"]["t_transfer"].payload


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_updates_airtime_fields_by_type() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_airtime_update",
        phone_number="2348000000936",
        channel="whatsapp",
        last_message_text="Change the airtime to 3k on Airtel",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["airtime"],
                    amount=3000,
                    network="Airtel",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["last_interrupt"].task_ids == ["t_airtime"]
    assert updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_airtime"].payload["amount"] == 3000
    assert updates["tasks"]["t_airtime"].payload["network"] == "Airtel"
    assert "idempotency_key" not in updates["tasks"]["t_airtime"].payload
    assert updates["tasks"]["t_transfer"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_transfer"].payload["amount"] == 10000


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_ambiguous_amount_asks_clarification() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_amount_ambiguous",
        phone_number="2348000000937",
        channel="whatsapp",
        last_message_text="Change the amount to 5k",
        waves=[["t_gaines", "t_tolu"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu"]),
        loaded_context={"language": "en"},
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "confirmation": {"summary": "Confirm Gaines", "snapshot": {"amount": 10000}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm Tolu", "snapshot": {"amount": 2000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.93,
                    detected_language="English",
                    amount=5000,
                )
            ),
            "services": {"transfer": _TransferNeedsConfirmationWorker()},
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["tasks"] == state.tasks
    assert updates["outbox"][0]["type"] == "say"
    assert "which" in updates["outbox"][0]["text"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_updates_batch_source_account_by_bank() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_source_bank",
        phone_number="2348000000938",
        channel="whatsapp",
        last_message_text="Use GTBank instead",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.94,
                    detected_language="English",
                    source_bank_name="GTBank",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert set(updates["last_interrupt"].task_ids) == {"t_transfer", "t_airtime"}
    assert updates["tasks"]["t_transfer"].payload["source_account_id"] is None
    assert updates["tasks"]["t_transfer"].payload["source_bank_name"] == "GTBank"
    assert updates["tasks"]["t_transfer"].payload["source_affinity_mode"] == "explicit"
    assert updates["tasks"]["t_airtime"].payload["source_account_id"] is None
    assert updates["tasks"]["t_airtime"].payload["source_bank_name"] == "GTBank"
    assert updates["tasks"]["t_airtime"].payload["source_affinity_mode"] == "explicit"
    assert "idempotency_key" not in updates["tasks"]["t_transfer"].payload
    assert "idempotency_key" not in updates["tasks"]["t_airtime"].payload


@pytest.mark.asyncio
async def test_pending_account_switch_with_bank_reference_updates_confirmation_source() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_source_switch_guard",
        phone_number="2348000000939",
        channel="telegram",
        last_message_text="Change source account to first bank",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {"id": "acct-access", "bank_name": "Access Bank", "account_number": "0000000003"},
                {"id": "acct-first", "bank_name": "First Bank", "account_number": "0000000001"},
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="switch_intent",
                    confidence=0.9,
                    detected_language="English",
                    target_intent="account",
                    target_texts=["First Bank"],
                    reason="user wants to use First Bank for the pending confirmation",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_transfer"].payload["source_account_id"] == "acct-first"
    assert updates["tasks"]["t_transfer"].payload["source_bank_name"] == "First Bank"
    assert updates["tasks"]["t_transfer"].payload["source_account_number"] == "0000000001"
    assert updates["tasks"]["t_transfer"].payload["source_affinity_mode"] == "explicit"
    assert updates["tasks"]["t_airtime"].payload["source_account_id"] == "acct-first"
    assert updates["tasks"]["t_airtime"].payload["source_bank_name"] == "First Bank"
    assert updates["tasks"]["t_airtime"].payload["source_account_number"] == "0000000001"
    assert updates["tasks"]["t_airtime"].payload["source_affinity_mode"] == "explicit"
    assert "idempotency_key" not in updates["tasks"]["t_transfer"].payload
    assert "idempotency_key" not in updates["tasks"]["t_airtime"].payload


@pytest.mark.asyncio
async def test_pending_action_edit_ambiguity_blocks_account_switch_router() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_account_switch_router_block",
        phone_number="2348000000941",
        channel="telegram",
        last_message_text="Change source account to first bank",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {"id": "acct-access", "bank_name": "Access Bank", "account_number": "0000000003"},
                {"id": "acct-first", "bank_name": "First Bank", "account_number": "0000000001"},
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    planner = _PendingActionEditThenRoutePlanner(
        PendingActionEditDecision(operation="unclear", confidence=0.95),
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.93,
            detected_language="English",
            target_intent="account",
            target_mode="new",
            reason="old router misclassified source edit as account management",
        ),
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["tasks"] == state.tasks
    assert updates["tasks"]["t_transfer"].payload["source_bank_name"] == "Access Bank"
    assert updates["tasks"]["t_airtime"].payload["source_bank_name"] == "Access Bank"
    assert updates["outbox"][0]["type"] == "say"
    assert "which" in updates["outbox"][0]["text"].lower()


@pytest.mark.asyncio
async def test_pending_action_edit_ambiguity_blocks_same_flow_switch_router() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_same_flow_router_block",
        phone_number="2348000000942",
        channel="telegram",
        last_message_text="Change it to 20k",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            )
        },
    )
    planner = _PendingActionEditThenRoutePlanner(
        PendingActionEditDecision(operation="unclear", confidence=0.95),
        InterruptRouteDecision(
            decision="switch_intent",
            confidence=0.93,
            detected_language="English",
            target_intent="transfer",
            target_mode="new",
            reason="old router misclassified edit as same-flow switch",
        ),
    )
    config: RunnableConfig = {"configurable": {"task_planner": planner, "services": {}}, "recursion_limit": 50}

    updates = await handle_pending_interrupt(state, config)

    assert planner.route_calls == 0
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["tasks"] == state.tasks
    assert updates["tasks"]["t_transfer"].stage == TaskStage.AWAITING_CONFIRMATION
    assert updates["tasks"]["t_transfer"].payload["amount"] == 10000
    assert updates["tasks"]["t_transfer"].payload["confirmation"] == {
        "summary": "Confirm transfer",
        "snapshot": {"amount": 10000},
    }
    assert updates["outbox"][0]["type"] == "say"
    assert "which" in updates["outbox"][0]["text"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_source_edit_can_scope_to_transfer_only() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_source_transfer_only",
        phone_number="2348000000940",
        channel="telegram",
        last_message_text="Use GTBank for the transfer only",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {"id": "acct-access", "bank_name": "Access Bank", "account_number": "0000000003"},
                {"id": "acct-gtb", "bank_name": "GTBank", "account_number": "0000000002"},
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.9,
                    detected_language="English",
                    target_types=["transfer"],
                    source_bank_name="GTBank",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_transfer"].payload["source_account_id"] == "acct-gtb"
    assert updates["tasks"]["t_transfer"].payload["source_bank_name"] == "GTBank"
    assert updates["tasks"]["t_transfer"].payload["source_account_number"] == "0000000002"
    assert updates["tasks"]["t_transfer"].payload["source_affinity_mode"] == "explicit"
    assert updates["tasks"]["t_airtime"].payload["source_account_id"] == "acct-access"
    assert updates["tasks"]["t_airtime"].payload["source_bank_name"] == "Access Bank"


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_updates_pooled_funding_split() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_funding_split",
        phone_number="2348000000941",
        channel="telegram",
        last_message_text="Make it 20k from Access and 15k from First Bank",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 35000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "6000000003",
                    "source_affinity_mode": "auto",
                    "funding_plan": {
                        "is_single_source": False,
                        "steps": [
                            {"account_id": "acct-access", "amount": 30000, "bank_name": "Access Bank"},
                            {"account_id": "acct-first", "amount": 5000, "bank_name": "First Bank"},
                        ],
                    },
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 35000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["transfer"],
                    funding_splits=[
                        {"bank_name": "Access Bank", "amount": 20000},
                        {"bank_name": "First Bank", "amount": 15000},
                    ],
                )
            ),
            "services": {"transfer": _TransferNeedsConfirmationWorker()},
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    payload = updates["tasks"]["t_transfer"].payload
    assert updates["pending_interrupt"] is None
    assert payload["explicit_split"] == {"Access Bank": 20000.0, "First Bank": 15000.0}
    assert payload["source_accounts"] == ["Access Bank", "First Bank"]
    assert payload["use_dual_accounts"] is True
    assert payload["funding_plan"] is None
    assert payload["source_account_id"] is None
    assert payload["source_bank_name"] is None
    assert payload["source_affinity_mode"] == "explicit"
    assert "idempotency_key" not in payload


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_can_disable_pooled_funding() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_semantic_funding_single",
        phone_number="2348000000942",
        channel="telegram",
        last_message_text="Don't pool it, use one account",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={"language": "en"},
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 35000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "use_dual_accounts": True,
                    "source_accounts": ["Access Bank", "First Bank"],
                    "explicit_split": {"Access Bank": 30000, "First Bank": 5000},
                    "funding_plan": {
                        "is_single_source": False,
                        "steps": [
                            {"account_id": "acct-access", "amount": 30000, "bank_name": "Access Bank"},
                            {"account_id": "acct-first", "amount": 5000, "bank_name": "First Bank"},
                        ],
                    },
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 35000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.9,
                    detected_language="English",
                    target_types=["transfer"],
                    use_dual_accounts=False,
                )
            ),
            "services": {"transfer": _TransferNeedsConfirmationWorker()},
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    payload = updates["tasks"]["t_transfer"].payload
    assert updates["pending_interrupt"] is None
    assert payload["use_dual_accounts"] is False
    assert payload["source_accounts"] is None
    assert payload["explicit_split"] is None
    assert payload["funding_plan"] is None
    assert "idempotency_key" not in payload


@pytest.mark.asyncio
async def test_semantic_confirmation_edit_invalidates_previous_pin_before_reconfirming() -> None:
    state = OrchestratorState(
        user_id="u_confirm_edit_pin_invalidated",
        phone_number="2348000000933",
        channel="whatsapp",
        last_message_text="For the transaction, the narration is for groceries",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pin_verified=True,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolulope Johnson",
                    "recipient_account": "2010000003",
                    "recipient_bank_name": "First Bank",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["transfer"],
                    narration="for groceries",
                )
            ),
            "services": {
                "transfer": _TransferExecutesWhenPinVerifiedWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pin_verified"] is False
    assert interrupt_updates["last_callback"] is None
    assert interrupt_updates["pending_interrupt"] is None
    assert interrupt_updates["tasks"]["t_transfer"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_transfer"].payload["narration"] == "for groceries"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_transfer", "t_airtime"}
    assert not any(entry.get("type") == "receipt" for entry in wave_updates.get("outbox", []))
    assert "receipt" not in wave_updates["tasks"]["t_transfer"].payload


@pytest.mark.asyncio
async def test_added_airtime_batch_overwrites_stale_single_transfer_async_group_on_pin_execution() -> None:
    state = OrchestratorState(
        user_id="u_added_airtime_stale_single_group",
        phone_number="2348000000934",
        channel="whatsapp",
        waves=[["t_transfer", "t_airtime"]],
        current_wave_index=0,
        pin_verified=True,
        last_callback={"pin_verified": True, "flow_type": "transfer"},
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolulope Johnson",
                    "recipient_account": "2010000003",
                    "recipient_bank_name": "First Bank",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                    "async_group_id": "old-single-transfer-group",
                    "async_group_size": 1,
                    "async_group_kind": "single",
                    "async_group_index": 1,
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "services": {
                "transfer": _TransferExecutesWhenPinVerifiedWorker(),
                "airtime": _AirtimeExecutesWhenPinVerifiedWorker(),
            },
        },
        "recursion_limit": 50,
    }

    approved_updates = await handle_pending_interrupt(state, config)

    assert approved_updates["pending_interrupt"] is None
    assert approved_updates["tasks"]["t_transfer"].stage == TaskStage.EXECUTING
    assert approved_updates["tasks"]["t_airtime"].stage == TaskStage.EXECUTING

    state = _apply(state, approved_updates)
    wave_updates = await advance_wave(state, config)

    transfer_payload = wave_updates["tasks"]["t_transfer"].payload
    airtime_payload = wave_updates["tasks"]["t_airtime"].payload
    assert transfer_payload["async_group_id"] == airtime_payload["async_group_id"]
    assert transfer_payload["async_group_size"] == 2
    assert airtime_payload["async_group_size"] == 2
    assert transfer_payload["async_group_kind"] == "mixed_batch"
    assert airtime_payload["async_group_kind"] == "mixed_batch"

    state = _apply(state, wave_updates)
    final_updates = await finalize(state, config)

    final_text = "\n".join(entry.get("text", "") for entry in final_updates["outbox"])
    assert "Your transactions are being processed." in final_text
    assert "Transfer of" not in final_text


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_adds_airtime_to_single_transfer_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_single_confirm_add_airtime",
        phone_number="2348000000928",
        channel="whatsapp",
        last_message_text="Also buy me 1k airtime",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["airtime"],
                    add_instruction="buy me 1k airtime",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    assert "t_transfer" in interrupt_updates["tasks"]
    added_airtime_ids = [task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "airtime"]
    assert len(added_airtime_ids) == 1
    added_airtime_id = added_airtime_ids[0]
    assert interrupt_updates["waves"] == [["t_transfer", added_airtime_id]]
    assert interrupt_updates["tasks"]["t_transfer"].stage == TaskStage.AWAITING_CONFIRMATION
    assert interrupt_updates["tasks"][added_airtime_id].payload["amount"] == 1000
    assert interrupt_updates["tasks"][added_airtime_id].payload["source_account_id"] == "acct-access"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_transfer", added_airtime_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_transfer", added_airtime_id}
    assert "Confirm transfer" in confirmation_entry["summary"]
    assert "airtime" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_adds_data_to_single_transfer_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_single_confirm_add_data",
        phone_number="2348000000933",
        channel="whatsapp",
        last_message_text="Also buy me 1k MTN data",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                    "idempotency_key": "idem-transfer",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["data"],
                    add_instruction="buy me 1k MTN data",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "data": _DataNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    added_data_ids = [task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "data"]
    assert len(added_data_ids) == 1
    added_data_id = added_data_ids[0]
    assert interrupt_updates["waves"] == [["t_transfer", added_data_id]]
    assert interrupt_updates["tasks"]["t_transfer"].stage == TaskStage.AWAITING_CONFIRMATION
    assert interrupt_updates["tasks"][added_data_id].payload["amount"] == 1000
    assert interrupt_updates["tasks"][added_data_id].payload["source_account_id"] == "acct-access"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_transfer", added_data_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_transfer", added_data_id}
    assert "Confirm transfer" in confirmation_entry["summary"]
    assert "data" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_adds_transfer_to_single_data_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_single_data_confirm_add_transfer",
        phone_number="2348000000934",
        channel="whatsapp",
        last_message_text="Also send 2k to Tolu",
        waves=[["t_data"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "plan_code": "MD101",
                    "plan_name": "MTN 1GB",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "0000000003",
                    "confirmation": {"summary": "Confirm data", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-data",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["transfer"],
                    add_instruction="send 2k to Tolu",
                )
            ),
            "services": {
                "data": _DataNeedsConfirmationWorker(),
                "transfer": _TransferNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_transfer_ids = [
        task_id
        for task_id, task in interrupt_updates["tasks"].items()
        if task.type == "transfer" and task_id != "t_data"
    ]
    assert len(added_transfer_ids) == 1
    added_transfer_id = added_transfer_ids[0]
    assert interrupt_updates["waves"] == [["t_data", added_transfer_id]]
    assert interrupt_updates["tasks"]["t_data"].stage == TaskStage.AWAITING_CONFIRMATION
    assert interrupt_updates["tasks"][added_transfer_id].payload["amount"] == 2000
    assert interrupt_updates["tasks"][added_transfer_id].payload["recipient_name"] == "Tolu"
    assert interrupt_updates["tasks"][added_transfer_id].payload["source_account_id"] == "acct-access"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_data", added_transfer_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_data", added_transfer_id}
    assert "Confirm data" in confirmation_entry["summary"]
    assert "transfer" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_adds_airtime_to_single_data_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_single_data_confirm_add_airtime",
        phone_number="2348000000936",
        channel="whatsapp",
        last_message_text="Also buy me 1k airtime",
        waves=[["t_data"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_data"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_data": TaskSpec(
                id="t_data",
                type="data",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "network": "MTN",
                    "target_phone": "08162511023",
                    "plan_code": "MD101",
                    "plan_name": "MTN 1GB",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "0000000003",
                    "confirmation": {"summary": "Confirm data", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-data",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["airtime"],
                    add_instruction="buy me 1k airtime",
                )
            ),
            "services": {
                "data": _DataNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_airtime_ids = [task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "airtime"]
    assert len(added_airtime_ids) == 1
    added_airtime_id = added_airtime_ids[0]
    assert interrupt_updates["waves"] == [["t_data", added_airtime_id]]
    assert interrupt_updates["tasks"]["t_data"].stage == TaskStage.AWAITING_CONFIRMATION
    assert interrupt_updates["tasks"][added_airtime_id].payload["amount"] == 1000
    assert interrupt_updates["tasks"][added_airtime_id].payload["source_account_id"] == "acct-access"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_data", added_airtime_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_data", added_airtime_id}
    assert "Confirm data" in confirmation_entry["summary"]
    assert "airtime" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_adds_data_to_single_airtime_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_single_airtime_confirm_add_data",
        phone_number="2348000000935",
        channel="whatsapp",
        last_message_text="Also buy me 1k MTN data",
        waves=[["t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "0000000003",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["data"],
                    add_instruction="buy me 1k MTN data",
                )
            ),
            "services": {
                "airtime": _AirtimeNeedsConfirmationWorker(),
                "data": _DataNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_data_ids = [task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "data"]
    assert len(added_data_ids) == 1
    added_data_id = added_data_ids[0]
    assert interrupt_updates["waves"] == [["t_airtime", added_data_id]]
    assert interrupt_updates["tasks"]["t_airtime"].stage == TaskStage.AWAITING_CONFIRMATION
    assert interrupt_updates["tasks"][added_data_id].payload["amount"] == 1000
    assert interrupt_updates["tasks"][added_data_id].payload["source_account_id"] == "acct-access"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_airtime", added_data_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_airtime", added_data_id}
    assert "Confirm airtime" in confirmation_entry["summary"]
    assert "data" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_action_edit_adds_transfer_to_single_airtime_confirmation() -> None:
    state = OrchestratorState(
        user_id="u_single_airtime_confirm_add_transfer",
        phone_number="2348000000937",
        channel="whatsapp",
        last_message_text="Also send 2k to Tolu",
        waves=[["t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "MTN",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "0000000003",
                    "confirmation": {"summary": "Confirm airtime", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["transfer"],
                    add_instruction="send 2k to Tolu",
                )
            ),
            "services": {
                "airtime": _AirtimeNeedsConfirmationWorker(),
                "transfer": _TransferNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_transfer_ids = [
        task_id
        for task_id, task in interrupt_updates["tasks"].items()
        if task.type == "transfer" and task_id != "t_airtime"
    ]
    assert len(added_transfer_ids) == 1
    added_transfer_id = added_transfer_ids[0]
    assert interrupt_updates["waves"] == [["t_airtime", added_transfer_id]]
    assert interrupt_updates["tasks"]["t_airtime"].stage == TaskStage.AWAITING_CONFIRMATION
    assert interrupt_updates["tasks"][added_transfer_id].payload["amount"] == 2000
    assert interrupt_updates["tasks"][added_transfer_id].payload["recipient_name"] == "Tolu"
    assert interrupt_updates["tasks"][added_transfer_id].payload["source_account_id"] == "acct-access"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_airtime", added_transfer_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_airtime", added_transfer_id}
    assert "Confirm airtime" in confirmation_entry["summary"]
    assert "transfer" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_semantic_pending_add_self_airtime_uses_context_phone_before_prompting() -> None:
    state = OrchestratorState(
        user_id="u_single_confirm_add_self_airtime",
        phone_number="2348162511023",
        channel="whatsapp",
        last_message_text="Also buy me 1k airtime",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adeyemi",
                    "recipient_account": "2010000002",
                    "recipient_bank_name": "GTBank",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.94,
                    detected_language="English",
                    target_types=["airtime"],
                    add_instruction="buy me 1k airtime",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AmountOnlyAirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_airtime_id = next(task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "airtime")
    assert interrupt_updates["tasks"][added_airtime_id].payload["is_self"] is True
    assert "recipient_phone" not in interrupt_updates["tasks"][added_airtime_id].payload

    state = _apply(state, interrupt_updates)
    airtime_worker = AirtimeWorker(
        extractor=_NetworkFollowupAirtimeExtractor(),
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
        redis_client=None,
    )
    execution_config: RunnableConfig = {
        "configurable": {
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": airtime_worker,
            },
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    wave_updates = await advance_wave(state, execution_config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_transfer", added_airtime_id}
    assert wave_updates["tasks"][added_airtime_id].payload["recipient_phone"] == "08162511023"
    assert wave_updates["tasks"][added_airtime_id].payload["network"] == "MTN"


@pytest.mark.asyncio
async def test_pending_add_task_uses_semantic_route_when_edit_target_type_is_wrong() -> None:
    state = OrchestratorState(
        user_id="u_single_confirm_add_airtime_semantic_override",
        phone_number="2348000000931",
        channel="whatsapp",
        last_message_text="Also buy me 1k airtime",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-first",
                    "bank_name": "First Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                },
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m2",
                },
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-first",
                    "source_bank_name": "First Bank",
                    "source_account_number": "0000000001",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.91,
                    detected_language="English",
                    target_types=["transfer"],
                    target_intent="transfer",
                    add_instruction="buy me 1k airtime",
                ),
                semantic_decision=SemanticRouteDecision(
                    decision="domain_airtime",
                    confidence=0.96,
                    target_intent="airtime",
                    expected_transaction_executors=["airtime"],
                ),
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_airtime_ids = [task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "airtime"]
    added_transfer_ids = [
        task_id
        for task_id, task in interrupt_updates["tasks"].items()
        if task.type == "transfer" and task_id != "t_transfer"
    ]
    assert len(added_airtime_ids) == 1
    assert added_transfer_ids == []
    added_airtime_id = added_airtime_ids[0]
    assert interrupt_updates["waves"] == [["t_transfer", added_airtime_id]]
    assert interrupt_updates["tasks"][added_airtime_id].payload["amount"] == 1000
    assert interrupt_updates["tasks"][added_airtime_id].payload["source_account_id"] == "acct-first"

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_transfer", added_airtime_id}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert "Which bank is that for?" not in confirmation_entry.get("summary", "")
    assert "airtime" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_pending_add_task_semantic_route_uses_fresh_instruction_context_only() -> None:
    state = OrchestratorState(
        user_id="u_single_confirm_add_airtime_context_isolated",
        phone_number="2348000000932",
        channel="whatsapp",
        last_message_text="Also buy me 1k airtime",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-access",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adeyemi",
                    "recipient_account": "2010000002",
                    "recipient_bank_name": "GTBank",
                    "source_account_id": "acct-access",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                },
            ),
        },
    )

    def semantic_decision_from_context(context: str) -> SemanticRouteDecision:
        if "Tolu" in context or "GTBank" in context:
            return SemanticRouteDecision(
                decision="domain_transfer",
                confidence=0.9,
                target_intent="transfer",
                expected_transaction_executors=["transfer"],
            )
        return SemanticRouteDecision(
            decision="domain_airtime",
            confidence=0.96,
            target_intent="airtime",
            expected_transaction_executors=["airtime"],
        )

    planner = _PendingActionEditPlanner(
        PendingActionEditDecision(
            operation="add_tasks",
            confidence=0.92,
            detected_language="English",
            target_types=["transfer"],
            target_intent="transfer",
            add_instruction="buy me 1k airtime",
        ),
        semantic_decision=semantic_decision_from_context,
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    added_airtime_ids = [task_id for task_id, task in interrupt_updates["tasks"].items() if task.type == "airtime"]
    assert len(added_airtime_ids) == 1
    assert "Tolu" not in planner.semantic_contexts[-1]
    assert "GTBank" not in planner.semantic_contexts[-1]

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert "Which bank is that for?" not in confirmation_entry.get("summary", "")
    assert "airtime" in confirmation_entry["summary"].lower()


@pytest.mark.asyncio
async def test_confirmation_remove_amount_referenced_task_from_batch() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_remove_by_amount",
        phone_number="2348000000922",
        channel="whatsapp",
        last_message_text="Remove the 5k one",
        waves=[["t_gaines", "t_tolu", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_number": "0000000002",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 5000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 5000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                },
            ),
        },
        task_results={
            "t_gaines": {"cached": "transfer"},
            "t_tolu": {"cached": "transfer"},
            "t_airtime": {"cached": "airtime"},
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="remove_tasks",
                    confidence=0.95,
                    detected_language="English",
                    target_texts=["5k"],
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    assert "t_tolu" not in interrupt_updates["tasks"]
    assert "t_tolu" not in interrupt_updates["task_results"]
    assert "t_tolu" in interrupt_updates["removed_confirmation_tasks"]
    assert interrupt_updates["waves"] == [["t_gaines", "t_airtime"]]
    assert interrupt_updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_gaines"].payload["skip_extraction"] is True
    assert interrupt_updates["tasks"]["t_airtime"].payload["skip_extraction"] is True

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_gaines", "t_airtime"}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_gaines", "t_airtime"}


@pytest.mark.asyncio
async def test_confirmation_remove_all_matching_amount_tasks_from_batch() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_remove_all_by_amount",
        phone_number="2348000000923",
        channel="whatsapp",
        last_message_text="Remove all 5k transactions",
        waves=[["t_gaines", "t_tolu", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_number": "0000000002",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 5000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 5000}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 5000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 5000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="remove_tasks",
                    confidence=0.95,
                    detected_language="English",
                    target_texts=["all 5k transactions"],
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    assert set(interrupt_updates["removed_confirmation_tasks"]) == {"t_gaines", "t_tolu"}
    assert set(interrupt_updates["tasks"]) == {"t_airtime"}
    assert interrupt_updates["waves"] == [["t_airtime"]]
    assert interrupt_updates["tasks"]["t_airtime"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_airtime"].payload["skip_extraction"] is True

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert wave_updates["pending_interrupt"].task_ids == ["t_airtime"]


@pytest.mark.asyncio
async def test_confirmation_remove_multiple_referenced_tasks_from_batch() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_remove_multiple_refs",
        phone_number="2348000000924",
        channel="whatsapp",
        last_message_text="Remove Tolu and airtime",
        waves=[["t_gaines", "t_tolu", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_number": "0000000002",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 5000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 5000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="remove_tasks",
                    confidence=0.95,
                    detected_language="English",
                    target_texts=["Tolu", "airtime"],
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    interrupt_updates = await handle_pending_interrupt(state, config)

    assert interrupt_updates["pending_interrupt"] is None
    assert set(interrupt_updates["removed_confirmation_tasks"]) == {"t_tolu", "t_airtime"}
    assert set(interrupt_updates["tasks"]) == {"t_gaines"}
    assert interrupt_updates["waves"] == [["t_gaines"]]
    assert interrupt_updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert interrupt_updates["tasks"]["t_gaines"].payload["skip_extraction"] is True

    state = _apply(state, interrupt_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert wave_updates["pending_interrupt"].task_ids == ["t_gaines"]


@pytest.mark.parametrize("restore_text", ["Add back", "I mean the 5k one"])
@pytest.mark.asyncio
async def test_confirmation_restore_single_removed_task_by_generic_or_amount_reference(restore_text: str) -> None:
    removed_tolu = TaskSpec(
        id="t_tolu",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 5000,
            "recipient_name": "Tolu Adebayo",
            "recipient_resolved_name": "Tolu Adebayo",
            "recipient_account": "2010000001",
            "recipient_bank_name": "Access Bank",
            "source_account_id": "acct-gtb",
            "source_bank_name": "GTBank",
            "source_account_number": "0000000002",
            "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 5000}},
        },
    )
    state = OrchestratorState(
        user_id="u_mixed_confirm_restore_removed_transfer",
        phone_number="2348000000925",
        channel="whatsapp",
        last_message_text=restore_text,
        waves=[["t_gaines", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_airtime"]),
        removed_confirmation_tasks={
            "t_tolu": {
                "task": removed_tolu,
                "wave_index": 0,
                "position": 1,
                "task_result": {"cached": "transfer"},
            }
        },
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_number": "0000000002",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="restore_tasks",
                    confidence=0.95,
                    detected_language="English",
                    target_texts=[] if restore_text == "Add back" else ["5k"],
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    restore_updates = await handle_pending_interrupt(state, config)

    assert restore_updates["pending_interrupt"] is None
    assert restore_updates["removed_confirmation_tasks"] == {}
    assert restore_updates["waves"] == [["t_gaines", "t_tolu", "t_airtime"]]
    assert restore_updates["tasks"]["t_tolu"].payload["skip_extraction"] is True

    state = _apply(state, restore_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu", "t_airtime"}


@pytest.mark.asyncio
async def test_confirmation_collective_transfer_narration_does_not_start_new_mixed_flow() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_transfer_narration",
        phone_number="2348000000926",
        channel="whatsapp",
        last_message_text="Add narration for both transfer as groceries",
        waves=[["t_gaines", "t_tolu", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_number": "0000000002",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-gaines",
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 10000}},
                    "idempotency_key": "idem-tolu",
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-gtb",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                    "idempotency_key": "idem-airtime",
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="update_fields",
                    confidence=0.95,
                    detected_language="English",
                    target_types=["transfer"],
                    narration="groceries",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    updates = await handle_pending_interrupt(state, config)

    assert updates["pending_interrupt"] is None
    assert updates["last_interrupt"].task_ids == ["t_gaines", "t_tolu"]
    assert updates["tasks"]["t_gaines"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_gaines"].payload["narration"] == "groceries"
    assert updates["tasks"]["t_gaines"].payload["authored_narration"] == "groceries"
    assert "idempotency_key" not in updates["tasks"]["t_gaines"].payload
    assert updates["tasks"]["t_tolu"].stage == TaskStage.EXTRACTED
    assert updates["tasks"]["t_tolu"].payload["narration"] == "groceries"
    assert updates["tasks"]["t_tolu"].payload["authored_narration"] == "groceries"
    assert "idempotency_key" not in updates["tasks"]["t_tolu"].payload
    assert updates["tasks"]["t_airtime"].stage == TaskStage.AWAITING_CONFIRMATION
    assert "narration" not in updates["tasks"]["t_airtime"].payload
    assert updates["tasks"]["t_airtime"].payload["idempotency_key"] == "idem-airtime"

    state = _apply(state, updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu", "t_airtime"}
    assert wave_updates["tasks"]["t_gaines"].payload["narration"] == "groceries"
    assert wave_updates["tasks"]["t_tolu"].payload["narration"] == "groceries"
    assert "narration" not in wave_updates["tasks"]["t_airtime"].payload


@pytest.mark.asyncio
async def test_confirmation_restore_removed_airtime_rejoins_original_batch() -> None:
    state = OrchestratorState(
        user_id="u_mixed_confirm_restore_airtime",
        phone_number="2348000000921",
        channel="whatsapp",
        last_message_text="Remove airtime purchase",
        waves=[["t_gaines", "t_tolu", "t_airtime"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_gaines", "t_tolu", "t_airtime"]),
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-gtb",
                    "bank_name": "GTBank",
                    "account_number": "0000000002",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        tasks={
            "t_gaines": TaskSpec(
                id="t_gaines",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Gaines",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_account": "0760705267",
                    "recipient_bank_name": "Access",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm Gaines transfer", "snapshot": {"amount": 10000}},
                },
            ),
            "t_tolu": TaskSpec(
                id="t_tolu",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 5000,
                    "recipient_name": "Tolu Adebayo",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm Tolu transfer", "snapshot": {"amount": 5000}},
                },
            ),
            "t_airtime": TaskSpec(
                id="t_airtime",
                type="airtime",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 1000,
                    "recipient_phone": "08162511023",
                    "network": "mtn",
                    "source_account_id": "acct-gtb",
                    "source_bank_name": "GTBank",
                    "source_account_number": "0000000002",
                    "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _SequentialPendingActionEditPlanner(
                [
                    PendingActionEditDecision(
                        operation="remove_tasks",
                        confidence=0.95,
                        detected_language="English",
                        target_types=["airtime"],
                        target_texts=["airtime purchase"],
                    ),
                    PendingActionEditDecision(
                        operation="restore_tasks",
                        confidence=0.95,
                        detected_language="English",
                        target_types=["airtime"],
                        target_texts=["airtime"],
                    ),
                ]
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await handle_pending_interrupt(state, config))
    state = _apply(state, await advance_wave(state, config)).model_copy(
        update={"last_message_text": "Sorry, add the airtime back"}
    )
    restore_updates = await handle_pending_interrupt(state, config)

    assert restore_updates["pending_interrupt"] is None
    assert restore_updates["removed_confirmation_tasks"] == {}
    assert restore_updates["waves"] == [["t_gaines", "t_tolu", "t_airtime"]]
    assert restore_updates["tasks"]["t_airtime"].payload["source_account_id"] == "acct-gtb"
    assert restore_updates["tasks"]["t_airtime"].payload["skip_extraction"] is True

    state = _apply(state, restore_updates)
    wave_updates = await advance_wave(state, config)

    assert wave_updates["pending_interrupt"].kind == "confirmation"
    assert set(wave_updates["pending_interrupt"].task_ids) == {"t_gaines", "t_tolu", "t_airtime"}
    confirmation_entry = next(entry for entry in wave_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t_gaines", "t_tolu", "t_airtime"}
    assert "Confirm airtime task" in confirmation_entry["summary"]


@pytest.mark.asyncio
async def test_confirmation_restore_single_removed_task_when_add_back_is_misclassified_as_add_task() -> None:
    removed_airtime = TaskSpec(
        id="t_airtime",
        type="airtime",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 1000,
            "recipient_phone": "08162511023",
            "network": "mtn",
            "source_account_id": "acct-access",
            "source_bank_name": "Access Bank",
            "source_account_number": "0000000003",
            "confirmation": {"summary": "Confirm airtime task", "snapshot": {"amount": 1000}},
        },
    )
    state = OrchestratorState(
        user_id="u_mixed_confirm_restore_add_back_misclassified",
        phone_number="2348000000926",
        channel="telegram",
        last_message_text="Sorry add it back",
        waves=[["t_transfer"]],
        current_wave_index=0,
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t_transfer"]),
        removed_confirmation_tasks={
            "t_airtime": {
                "task": removed_airtime,
                "wave_index": 0,
                "position": 1,
                "task_result": {"cached": "airtime"},
            }
        },
        tasks={
            "t_transfer": TaskSpec(
                id="t_transfer",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 2000,
                    "recipient_name": "Tolu",
                    "recipient_resolved_name": "Tolu Adebayo",
                    "recipient_account": "2010000001",
                    "recipient_bank_name": "Access Bank",
                    "source_account_id": "acct-access",
                    "source_bank_name": "Access Bank",
                    "source_account_number": "0000000003",
                    "narration": "Urgent 2k",
                    "confirmation": {"summary": "Confirm transfer", "snapshot": {"amount": 2000}},
                },
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _PendingActionEditPlanner(
                PendingActionEditDecision(
                    operation="add_tasks",
                    confidence=0.92,
                    detected_language="English",
                    target_types=["transfer"],
                    target_intent="transfer",
                    add_instruction="Sorry add it back",
                    reason="misclassified restore as add",
                )
            ),
            "services": {
                "transfer": _TransferNeedsConfirmationWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
        },
        "recursion_limit": 50,
    }

    restore_updates = await handle_pending_interrupt(state, config)

    assert restore_updates["pending_interrupt"] is None
    assert restore_updates["removed_confirmation_tasks"] == {}
    assert restore_updates["waves"] == [["t_transfer", "t_airtime"]]
    assert set(restore_updates["tasks"]) == {"t_transfer", "t_airtime"}
    assert restore_updates["tasks"]["t_airtime"].payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_cancelled_mixed_flow_then_fresh_self_airtime_reuses_context_phone() -> None:
    first_plan = PlannerOutput(
        primary_intent="mixed",
        confidence=0.95,
        detected_language="English",
        normalized_instruction="send 10k to mum and buy me 5k airtime",
        tasks=[
            PlannedTask(
                task_id="t_transfer",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t_airtime",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy 5k airtime",
                parameters=TaskParameters(amount=5000),
                risk="MONEY_MOVE",
            ),
        ],
    )
    second_plan = PlannerOutput(
        primary_intent="airtime",
        confidence=0.95,
        detected_language="English",
        normalized_instruction="buy me 5k airtime",
        tasks=[
            PlannedTask(
                task_id="t_airtime_restart",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy 5k airtime",
                parameters=TaskParameters(amount=5000),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = _SequentialPlanner([first_plan, second_plan])

    plan_config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {
                "transfer": _TransferNeedsInputWorker(),
                "airtime": _AirtimeNeedsConfirmationWorker(),
            },
            "redis_client": None,
            "queue": None,
            "beneficiary_suggestion_service": None,
        },
        "recursion_limit": 50,
    }

    state = _planner_state().model_copy(update={"last_message_text": "Send 10k to mum and buy me 5k airtime"})
    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, plan_config))
    assert set(state.tasks.keys()) == {"t_transfer", "t_airtime"}

    state = _apply(state, await advance_wave(state, plan_config))
    assert state.pending_interrupt is not None
    assert state.pending_interrupt.kind == "input"

    state = state.model_copy(update={"last_message_text": "Cancel"})
    state = _apply(state, await handle_pending_interrupt(state, plan_config))
    assert state.tasks == {}
    assert state.waves == []
    assert state.pending_interrupt is None
    assert state.final_response == render_cancelled_prompt("en")

    state = _apply(state, await finalize(state, plan_config))
    assert state.tasks == {}
    assert state.waves == []

    state = state.model_copy(update={"last_message_text": "Buy me 5k airtime"})
    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, plan_config))
    assert list(state.tasks.keys()) == ["t_airtime_restart"]

    airtime_worker = AirtimeWorker(
        extractor=_NetworkFollowupAirtimeExtractor(),
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
        redis_client=None,
    )
    execution_config: RunnableConfig = {
        "configurable": {
            "task_planner": planner,
            "services": {"airtime": airtime_worker},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await advance_wave(state, execution_config))
    assert state.pending_interrupt is not None
    assert state.pending_interrupt.kind == "input"
    assert state.pending_interrupt.task_ids == ["t_airtime_restart"]
    assert "recipient_phone" not in state.pending_interrupt.fields_by_task.get("t_airtime_restart", [])
    assert state.pending_interrupt.fields_by_task.get("t_airtime_restart", []) == ["network"]
    assert state.tasks["t_airtime_restart"].payload.get("recipient_phone") == "08000000900"

    state = state.model_copy(update={"last_message_text": "mtn"})
    state = _apply(state, await handle_pending_interrupt(state, execution_config))
    state = _apply(state, await advance_wave(state, execution_config))

    airtime_payload = state.tasks["t_airtime_restart"].payload
    assert state.pending_interrupt is not None
    assert state.pending_interrupt.kind == "confirmation"
    assert airtime_payload.get("recipient_phone") == "08000000900"
    assert airtime_payload.get("network") == "MTN"
