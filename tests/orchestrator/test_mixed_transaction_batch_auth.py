"""Regression tests for mixed transfer+airtime confirmation/auth batching."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.graphs.airtime.worker import AirtimeWorker
from apps.core.src.agent.orchestrator.models.domain import (
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.finalize import finalize
from apps.core.src.agent.orchestrator.nodes.ingest import ingest_message
from apps.core.src.agent.orchestrator.nodes.interrupt import handle_pending_interrupt
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.formatters.accounts import format_source_account_info_from_account_number
from shared.i18n import render_cancelled_prompt
from shared.types.planner import InterruptRouteDecision, PlannedTask, PlannerOutput, TaskParameters

SHARED_SOURCE_LINE = format_source_account_info_from_account_number(
    bank="Zenith Bank",
    account_number="0000009384",
    locale="en",
    balance=None,
)


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _SequentialPlanner:
    def __init__(self, outputs: list[PlannerOutput]) -> None:
        self._outputs = outputs
        self._idx = 0

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
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
    ) -> InterruptRouteDecision:
        del phone_number, context
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


class _TransferNeedsConfirmationWorker:
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


class _AirtimeNeedsConfirmationWorker:
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
    assert "Queued next after this step" in say_entry["text"]
    assert "airtime" in say_entry["text"].lower()
    assert say_entry["queue"]["queued_task_ids"] == ["t_airtime"]


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
    assert "Queued next after this step" in say_entry["text"]
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
