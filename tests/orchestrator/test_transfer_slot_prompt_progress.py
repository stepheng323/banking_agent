"""Transfer slot-filling prompt progression tests."""

import time

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize import finalize
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from banking.transfers.worker import TransferWorker
from shared.config.settings import settings
from shared.types.planner import ContextFrameFollowupDecision


class _MockNonTransferNeedsInputWorker:
    """Worker that returns NEEDS_INPUT with non-transfer fields (e.g. airtime)."""

    def __init__(self, required_fields: list[str], prompt: str) -> None:
        self.required_fields = required_fields
        self.prompt = prompt

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, pin_verified, context, user_message
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=self.required_fields,
            prompt=self.prompt,
        )


class _MockTransferNeedsInputWorker:
    def __init__(
        self,
        required_fields: list[str],
        prompt: str = "I need account details for this recipient.",
        details: dict | None = None,
    ) -> None:
        self.required_fields = required_fields
        self.prompt = prompt
        self.details = details or {}
        self.last_context: dict | None = None
        self.last_user_message: str | None = None
        self.call_count = 0
        self.called_with_payloads: list[dict] = []

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del pin_verified
        self.call_count += 1
        self.called_with_payloads.append(dict(payload))
        self.last_context = context
        self.last_user_message = user_message
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=self.required_fields,
            prompt=self.prompt,
            details=self.details,
        )


class _MockScheduleCountWorker:
    def __init__(self) -> None:
        self.last_context: dict | None = None
        self.last_payload: dict | None = None
        self.call_count = 0

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del user_message, pin_verified
        self.call_count += 1
        self.last_payload = dict(payload)
        self.last_context = context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Pending scheduled transactions: 2.",
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": [
                    {
                        "entity_id": "sch-transfer",
                        "label": "Transfer: ₦5,000 Mum • One Time at 8:00 AM Lagos time",
                        "data": {
                            "type": "scheduled_transaction",
                            "schedule_id": "sch-transfer",
                            "domain": "Transfer",
                            "domain_key": "transfer",
                            "amount": "₦5,000",
                            "target": "Mum",
                            "recurrence": "One Time",
                            "schedule_time": "8:00 AM Lagos time",
                            "status": "active",
                            "summary": "Transfer: ₦5,000 Mum • One Time at 8:00 AM Lagos time",
                        },
                    }
                ],
            },
        )


class _MockStallingScheduleWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> object:
        del payload, context, user_message, pin_verified

        class _Result:
            outcome = "deferred"
            patch: dict = {}
            response = None
            receipt = None
            required_fields: list = []
            details: dict = {}
            prompt = None
            update_message = None
            confirmation_summary = None
            confirmation_snapshot = None
            error = "deferred without interrupt"

        return _Result()


class _MockBeneficiaryRepo:
    def __init__(self, beneficiaries: list[dict]) -> None:
        self.beneficiaries = beneficiaries
        self.calls: list[tuple[str, str | None]] = []

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[dict]:
        self.calls.append((user_id, beneficiary_type))
        return self.beneficiaries


class _MockTransferResolveThenPromptWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        recipient_name = payload.get("recipient_name")
        if recipient_name == "Mum":
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "recipient_name": "Mum",
                    "recipient_resolved_name": "MERCY JOHNSON",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                },
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt="What's tolu's account number and bank?",
        )


class _MockTransferMixedConfirmWorker:
    def __init__(self) -> None:
        self.call_count = 0
        self.calls: list[str] = []
        self._tolu_first_turn = True

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        self.call_count += 1
        recipient_name = str(payload.get("recipient_name") or "")
        self.calls.append(recipient_name)

        if recipient_name.casefold() == "mum":
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                confirmation_snapshot={"amount": 10000, "recipient_name": "Mum"},
                confirmation_summary="Confirm Mum",
            )

        if self._tolu_first_turn:
            self._tolu_first_turn = False
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt="What's tolu's account number and bank?",
            )

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_snapshot={"amount": 10000, "recipient_name": "Tolu"},
            confirmation_summary="Confirm Tolu",
        )


def _build_state(
    *,
    last_interrupt: PendingInterrupt | None = None,
    last_message_text: str | None = None,
) -> OrchestratorState:
    return OrchestratorState(
        user_id="u_prompt_progress",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        last_interrupt=last_interrupt,
        last_message_text=last_message_text,
    )


async def test_grouped_prompt_when_account_and_bank_missing() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert "account number and bank" in text
    assert "I need account details for Tolu." not in text


async def test_transfer_known_amount_missing_recipient_prompt_is_natural() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state()
    state.tasks["t1"].payload = {"amount": 5000}
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Got ₦5,000.00. Who should I send it to?"


async def test_transfer_known_recipient_missing_amount_prompt_is_natural() -> None:
    worker = _MockTransferNeedsInputWorker(["amount"], prompt="How much would you like to send?")
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "I found Tolu. How much should I send?"


async def test_transfer_known_amount_and_recipient_missing_account_prompt_is_natural() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state()
    state.tasks["t1"].payload["amount"] = 5000
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Got ₦5,000.00 for Tolu. Please share the account number and bank."


async def test_prompt_only_account_number_when_bank_already_provided() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account"])
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert "account number for Tolu" in text
    assert "Which bank is that for?" not in text


async def test_prompt_only_bank_when_account_number_already_provided() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_bank_name"])
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert "Which bank is that for?" in text
    assert "account number for Tolu" not in text


async def test_data_known_network_missing_phone_prompt_is_natural() -> None:
    worker = _MockNonTransferNeedsInputWorker(["target_phone"], "Which line should I buy data for?")
    state = OrchestratorState(
        user_id="u_data_prompt_network",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"network": "MTN"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"data": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Got the MTN data. Which MTN line should I buy it for?"


async def test_data_known_phone_missing_network_prompt_is_natural() -> None:
    worker = _MockNonTransferNeedsInputWorker(["network"], "Which network is it on?")
    state = OrchestratorState(
        user_id="u_data_prompt_phone",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="data",
                stage=TaskStage.EXTRACTED,
                payload={"target_phone": "08162511023"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"data": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "I have 08162511023. Which network is it on?"


async def test_airtime_known_amount_missing_phone_prompt_is_natural() -> None:
    worker = _MockNonTransferNeedsInputWorker(["recipient_phone"], "Please provide the phone number.")
    state = OrchestratorState(
        user_id="u_airtime_prompt_amount",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 1000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"airtime": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Got ₦1,000.00 airtime. Which line should I buy it for?"


async def test_airtime_known_phone_missing_amount_prompt_is_natural() -> None:
    worker = _MockNonTransferNeedsInputWorker(["amount"], "Please provide the amount.")
    state = OrchestratorState(
        user_id="u_airtime_prompt_phone",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_phone": "08162511023"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"airtime": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "I have 08162511023. How much airtime should I buy?"


async def test_airtime_known_phone_missing_network_prompt_is_natural() -> None:
    worker = _MockNonTransferNeedsInputWorker(["network"], "Which network is it on?")
    state = OrchestratorState(
        user_id="u_airtime_prompt_network",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 1000, "recipient_phone": "08162511023"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"airtime": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Got ₦1,000.00 for 08162511023. Which network is it on?"


async def test_airtime_bare_purchase_missing_phone_and_amount_prompt_is_natural() -> None:
    worker = _MockNonTransferNeedsInputWorker(
        ["recipient_phone", "amount"], "Please provide the phone number and amount."
    )
    state = OrchestratorState(
        user_id="u_airtime_prompt_bare",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"airtime": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Sure. Who should I buy airtime for, and how much?"


async def test_bank_only_follow_up_prompts_for_account_number() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account"])
    previous_prompt = "What's Tolu's account number and bank?"
    state = _build_state(
        last_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_account", "recipient_bank_name"]},
            prompt=previous_prompt,
        ),
        last_message_text="Access bank",
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert worker.last_user_message == "Access bank"
    assert worker.last_context is not None
    assert worker.last_context["required_fields"] == ["recipient_account", "recipient_bank_name"]
    assert worker.last_context["previous_response"] == previous_prompt
    assert "account number for Tolu" in text
    assert "Which bank is that for?" not in text


async def test_amount_follow_up_preserves_raw_amount_reply_when_source_already_selected() -> None:
    worker = _MockTransferNeedsInputWorker(["amount"], prompt="How much would you like to send?")
    state = _build_state(
        last_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["amount"]},
            prompt="How much would you like to send?",
        ),
        last_message_text="20k",
    )
    state.tasks["t1"].payload.update(
        {
            "recipient_name": "Mum",
            "source_account_id": "acct-1",
            "amount": None,
        }
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.last_context is not None
    assert worker.last_context["required_fields"] == ["amount"]
    assert worker.last_user_message == "20k"


async def test_account_bank_follow_up_preserves_raw_reply_when_source_already_selected() -> None:
    worker = _MockTransferNeedsInputWorker(
        ["recipient_account", "recipient_bank_name"],
        prompt="What's Tolu's account number and bank?",
    )
    state = _build_state(
        last_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1"],
            fields_by_task={"t1": ["recipient_account", "recipient_bank_name"]},
            prompt="What's Tolu's account number and bank?",
        ),
        last_message_text="816 251 1027 First Bank",
    )
    state.tasks["t1"].payload.update(
        {
            "recipient_name": "Tolu",
            "source_account_id": "acct-1",
            "amount": 10000,
        }
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.last_context is not None
    assert worker.last_context["required_fields"] == ["recipient_account", "recipient_bank_name"]
    assert worker.last_user_message == "816 251 1027 First Bank"


async def test_multi_transfer_prompt_shows_alias_then_resolved_name_in_parentheses() -> None:
    worker = _MockTransferResolveThenPromptWorker()
    state = OrchestratorState(
        user_id="u_multi_names",
        phone_number="2348000000112",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 10000},
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "tolu", "amount": 10000},
            ),
        },
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
        last_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t1", "t2"],
            fields_by_task={
                "t1": ["recipient_account", "recipient_bank_name"],
                "t2": ["recipient_account", "recipient_bank_name"],
            },
            prompt="What's the account number and bank?",
        ),
        last_message_text="Mum 8162511023 Opay; still need tolu",
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert "Mum (MERCY JOHNSON)" in text
    assert "tolu's account number and bank" in text.lower()


async def test_transfer_handler_reloads_beneficiaries_when_context_list_is_empty() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    beneficiary_repo = _MockBeneficiaryRepo(
        [
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "GTBank",
                "bank_code": "058",
            }
        ]
    )
    state = _build_state()
    state.tasks["t1"].payload["recipient_name"] = "Mum"
    state.loaded_context["user_id"] = "user-1"
    state.loaded_context["beneficiaries"] = []
    config: RunnableConfig = {
        "configurable": {
            "services": {"transfer": worker},
            "beneficiary_repo": beneficiary_repo,
        },
        "recursion_limit": 50,
    }

    await advance_wave(state, config)

    assert beneficiary_repo.calls == [("user-1", "transfer")]
    assert worker.last_context is not None
    assert len(worker.last_context["beneficiaries"]) == 1
    assert worker.last_context["beneficiaries"][0]["alias"] == "Mum"


async def test_beneficiary_ambiguity_prompt_is_preserved() -> None:
    ambiguity_prompt = (
        "I found multiple matches for 'Tolu'. Which one did you mean?\n"
        "1. Tolu A • Access Bank • ****1234\n"
        "2. Tolu B • GTBank • ****5678\n"
        "Reply with the number or rephrase."
    )
    worker = _MockTransferNeedsInputWorker(
        ["beneficiary_id"],
        prompt=ambiguity_prompt,
        details={
            "ambiguity": "MULTIPLE_BENEFICIARIES",
            "candidates": [
                {"id": "bene-1", "label": "Tolu A • Access Bank • ****1234"},
                {"id": "bene-2", "label": "Tolu B • GTBank • ****5678"},
            ],
        },
    )
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    options_intent = updates["outbox"][0]

    assert options_intent["type"] == "show_options"
    assert "I found multiple matches for 'Tolu'. Which one did you mean?" in options_intent["title"]
    assert "Reply with the number or rephrase." in options_intent["title"]
    assert "1. Tolu A" not in options_intent["title"]
    assert "account number and bank" not in options_intent["title"]
    assert options_intent["task_ids"] == ["t1"]
    assert [opt["id"] for opt in options_intent["options"]] == ["bene-1", "bene-2"]
    assert options_intent["options"][0]["title"] == "Tolu A • Access Bank • ****1234"


async def test_amount_suggestion_prompt_emits_options_when_flag_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_channel_option_ux_v2", True)
    suggestion_prompt = "How much should I send to Tolu? I can use your last amount (₦5,000)."
    worker = _MockTransferNeedsInputWorker(
        ["amount"],
        prompt=suggestion_prompt,
        details={
            "option_context": "TRANSFER_AMOUNT_SUGGESTION",
            "options": [
                {"id": "1", "title": "Use ₦5,000"},
                {"id": "2", "title": "Enter a new amount"},
            ],
        },
    )
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert updates["outbox"][0]["type"] == "show_options"
    assert updates["outbox"][0]["title"] == suggestion_prompt
    assert [opt["id"] for opt in updates["outbox"][0]["options"]] == ["1", "2"]


async def test_source_account_prompt_emits_options_when_flag_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_channel_option_ux_v2", True)
    source_prompt = "*Which account would you like to use?*\n\n1. Access (···1234)\n2. GTBank (···5678)"
    worker = _MockTransferNeedsInputWorker(
        ["source_account_id"],
        prompt=source_prompt,
        details={
            "options": [
                {"id": "1", "title": "Access Bank (···1234)"},
                {"id": "2", "title": "GTBank (···5678)"},
            ],
        },
    )
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert updates["outbox"][0]["type"] == "show_options"
    assert "*Which account would you like to use?*" in updates["outbox"][0]["title"]
    assert "1. Access" not in updates["outbox"][0]["title"]
    assert [opt["id"] for opt in updates["outbox"][0]["options"]] == ["1", "2"]


async def test_superset_missing_fields_still_prompts_for_account_and_bank() -> None:
    """When required_fields contains extra fields beyond account+bank, the prompt should still be specific."""
    worker = _MockTransferNeedsInputWorker(["recipient_name", "recipient_account", "recipient_bank_name"])
    state = _build_state()
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert "account number and bank" in text
    assert "I need account details for Tolu." not in text


async def test_unsafe_recipient_name_falls_back_to_generic_prompt_label() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state()
    state.tasks["t1"].payload["recipient_name"] = "send's"
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"].lower()

    assert "send's account number and bank" not in text
    assert "please share the account number and bank for recipient." in text


async def test_transfer_handler_passes_referent_memory_to_worker() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state(last_message_text="send her 5k")
    now = int(time.time())
    frame = ContextFrame(
        frame_id="frame_bene_recent",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="bene-1",
                label="Mum",
                data={"id": "bene-1", "alias": "Mum"},
            )
        ],
        created_at_ts=now,
        ttl_seconds=600,
    )
    OrchestratorContextManager().push_frame(state, frame)
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.last_context is not None
    assert worker.last_context.get("referent_memory", {}).get("items")
    assert worker.last_context.get("resolved_referents", {}).get("recipient", {}).get("status") == "resolved"


async def test_transfer_handler_resolves_focused_beneficiary_referent_to_worker() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state(last_message_text="send her 5k")
    now = int(time.time())
    frame = ContextFrame(
        frame_id="frame_bene_focused",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="bene-1",
                label="Mum",
                data={
                    "id": "bene-1",
                    "alias": "Mum",
                    "account_name": "Mercy Johnson",
                    "account_number": "8162511023",
                    "bank_name": "Opay",
                    "bank_code": "100004",
                },
            )
        ],
        focus_index=0,
        created_at_ts=now,
        ttl_seconds=600,
    )
    OrchestratorContextManager().push_frame(state, frame)
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.last_context is not None
    resolved = worker.last_context.get("resolved_referents", {}).get("recipient", {})
    data = resolved.get("item", {}).get("data", {})
    assert resolved.get("status") == "resolved"
    assert data.get("account_number") == "8162511023"


async def test_ambiguous_beneficiary_referent_blocks_confirmation() -> None:
    worker = TransferWorker(
        validation_service=None,
        publisher=None,
        extractor=None,
        resolver_provider=None,
        bank_cache=None,
        transaction_repo=None,
    )
    state = OrchestratorState(
        user_id="u_prompt_referent_ambiguity",
        phone_number="2348000000100",
        channel="whatsapp",
        last_message_text="Send her 6k",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "recipient_name": "her",
                    "amount": 6000,
                    "source_account_id": "acct-1",
                    "skip_extraction": True,
                },
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Access Bank",
                    "account_number": "2010000003",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    now = int(time.time())
    frame = ContextFrame(
        frame_id="frame_tolu_beneficiaries",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="bene-access",
                label="Tolu Access",
                data={
                    "id": "bene-access",
                    "alias": "Tolu Access",
                    "account_name": "Tolu Adebayo",
                    "account_number": "2010000001",
                    "bank_name": "Access Bank",
                    "bank_code": "044",
                    "beneficiary_type": "transfer",
                },
            ),
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="bene-gtb",
                label="Tolu GTB",
                data={
                    "id": "bene-gtb",
                    "alias": "Tolu GTB",
                    "account_name": "Tolu Adeyemi",
                    "account_number": "2010000002",
                    "bank_name": "GTBank",
                    "bank_code": "058",
                    "beneficiary_type": "transfer",
                },
            ),
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="bene-first",
                label="Tolu First",
                data={
                    "id": "bene-first",
                    "alias": "Tolu First",
                    "account_name": "Tolulope Johnson",
                    "account_number": "2010000003",
                    "bank_name": "First Bank",
                    "bank_code": "011",
                    "beneficiary_type": "transfer",
                },
            ),
        ],
        created_at_ts=now,
        ttl_seconds=600,
    )
    OrchestratorContextManager().push_frame(state, frame)
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].fields_by_task == {"t1": ["referent_recipient_id"]}
    assert updates["outbox"][0]["type"] == "show_options"
    assert updates["outbox"][0]["title"] == "Which recipient did you mean?"
    assert [option["id"] for option in updates["outbox"][0]["options"]] == ["referent:1", "referent:2", "referent:3"]
    assert not any(entry.get("type") == "request_confirmation" for entry in updates["outbox"])


async def test_non_transfer_task_uses_contextual_airtime_prompt_not_transfer_formatter() -> None:
    """Airtime tasks should use the airtime slot formatter, not transfer recipient copy."""
    airtime_prompt = "What phone number should I send airtime to?"
    worker = _MockNonTransferNeedsInputWorker(["recipient_phone"], airtime_prompt)
    state = OrchestratorState(
        user_id="u_airtime",
        phone_number="2348000000100",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 2000},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"airtime": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)
    text = updates["outbox"][0]["text"]

    assert text == "Got ₦2,000.00 airtime. Which line should I buy it for?"
    assert "account details" not in text


async def test_multi_transfer_input_turn_executes_only_focused_interrupt_task() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = OrchestratorState(
        user_id="u_multi_focus",
        phone_number="2348000000111",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 10000, "source_account_id": "acct-1"},
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu", "amount": 10000, "source_account_id": "acct-1"},
            ),
        },
        waves=[["t1", "t2"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
        last_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t2"],
            fields_by_task={"t2": ["recipient_account", "recipient_bank_name"]},
            prompt="What's Tolu's account number and bank?",
        ),
        last_message_text="816 251 1027 First Bank",
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.call_count == 1
    assert worker.called_with_payloads[0].get("recipient_name") == "Tolu"
    assert worker.last_user_message == "816 251 1027 First Bank"


async def test_multi_transfer_confirmation_preserves_existing_waiting_task() -> None:
    worker = _MockTransferMixedConfirmWorker()
    state = OrchestratorState(
        user_id="u_multi_confirm_set",
        phone_number="2348000000113",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 10000, "source_account_id": "acct-1"},
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu", "amount": 10000, "source_account_id": "acct-1"},
            ),
        },
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
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    first_updates = await advance_wave(state, config)
    assert first_updates["pending_interrupt"].kind == "input"
    assert first_updates["pending_interrupt"].task_ids == ["t2"]
    assert first_updates["tasks"]["t1"].stage == TaskStage.AWAITING_CONFIRMATION
    assert first_updates["tasks"]["t2"].stage == TaskStage.EXTRACTED

    state.tasks = first_updates["tasks"]
    state.last_interrupt = first_updates["pending_interrupt"]
    state.pending_interrupt = None
    state.last_message_text = "816 251 1027 First Bank"

    second_updates = await advance_wave(state, config)

    assert worker.call_count == 3
    assert worker.calls.count("Mum") == 1

    second_interrupt = second_updates["pending_interrupt"]
    assert second_interrupt.kind == "confirmation"
    assert set(second_interrupt.task_ids) == {"t1", "t2"}

    confirmation_entry = next(entry for entry in second_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t1", "t2"}
    assert confirmation_entry["header"] == "Confirm Transactions"
    assert "Confirm Mum" in confirmation_entry["summary"]
    assert "Confirm Tolu" in confirmation_entry["summary"]


async def test_multi_transfer_fanout_task_ids_keep_all_recipients_in_confirmation() -> None:
    worker = _MockTransferMixedConfirmWorker()
    state = OrchestratorState(
        user_id="u_multi_confirm_fanout",
        phone_number="2348000000114",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Mum", "amount": 10000, "source_account_id": "acct-1"},
            ),
            "t1_r2": TaskSpec(
                id="t1_r2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Tolu", "amount": 10000, "source_account_id": "acct-1"},
            ),
        },
        waves=[["t1", "t1_r2"]],
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
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    first_updates = await advance_wave(state, config)
    assert first_updates["pending_interrupt"].kind == "input"
    assert first_updates["pending_interrupt"].task_ids == ["t1_r2"]
    assert first_updates["tasks"]["t1"].stage == TaskStage.AWAITING_CONFIRMATION
    assert first_updates["tasks"]["t1_r2"].stage == TaskStage.EXTRACTED

    state.tasks = first_updates["tasks"]
    state.last_interrupt = first_updates["pending_interrupt"]
    state.pending_interrupt = None
    state.last_message_text = "816 251 1027 First Bank"

    second_updates = await advance_wave(state, config)
    second_interrupt = second_updates["pending_interrupt"]
    assert second_interrupt.kind == "confirmation"
    assert set(second_interrupt.task_ids) == {"t1", "t1_r2"}

    confirmation_entry = next(entry for entry in second_updates["outbox"] if entry["type"] == "request_confirmation")
    assert set(confirmation_entry["task_ids"]) == {"t1", "t1_r2"}
    assert confirmation_entry["header"] == "Confirm Transactions"
    assert "Confirm Mum" in confirmation_entry["summary"]
    assert "Confirm Tolu" in confirmation_entry["summary"]


async def test_batch_confirmation_strips_name_mismatch_warning_line() -> None:
    warning = "You asked to send to Tolu, but the account resolved as TOLU ADEDAYO."
    state = OrchestratorState(
        user_id="u_multi_confirm_warning_strip",
        phone_number="2348000000115",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Tolu",
                    "name_mismatch_warning": warning,
                    "confirmation": {
                        "summary": f"{warning}\n\n₦10,000 → Tolu (Tolu Adedayo)\nAccess • 0760505261",
                        "snapshot": {"amount": 10000, "recipient_name": "Tolu"},
                    },
                    "source_account_id": "acct-1",
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "recipient_name": "Mum",
                    "confirmation": {
                        "summary": "₦10,000 → Mum (Mercy Johnson)\nOpay • 8162511023",
                        "snapshot": {"amount": 10000, "recipient_name": "Mum"},
                    },
                    "source_account_id": "acct-1",
                },
            ),
        },
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
    )

    config: RunnableConfig = {"configurable": {"services": {}}, "recursion_limit": 50}
    updates = await advance_wave(state, config)

    confirmation_entry = next(entry for entry in updates["outbox"] if entry["type"] == "request_confirmation")
    summary = confirmation_entry["summary"]

    assert "Confirm Transfers (2)" in summary
    assert warning not in summary
    assert "Tolu (Tolu Adedayo)" in summary
    assert "Mum (Mercy Johnson)" in summary


async def test_finalize_multi_transfer_summary_uses_alias_resolved_with_title_case() -> None:
    state = OrchestratorState(
        user_id="u_finalize_multi_case",
        phone_number="2348000000116",
        channel="whatsapp",
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 10000,
                    "recipient_name": "mum",
                    "recipient_resolved_name": "MERCY JOHNSON",
                    "recipient_bank_name": "Opay",
                    "recipient_account": "8162511023",
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.COMPLETED,
                payload={
                    "amount": 10000,
                    "recipient_name": "tolu",
                    "recipient_resolved_name": "GRACE NGOZI ADEBAYO",
                    "recipient_bank_name": "Access Bank",
                    "recipient_account": "0762511023",
                },
            ),
        },
        loaded_context={"language": "en"},
    )
    config: RunnableConfig = {
        "configurable": {
            "beneficiary_suggestion_service": None,
            "redis_client": None,
            "queue": None,
        }
    }

    updates = await finalize(state, config)

    say_entries = [entry for entry in updates["outbox"] if entry.get("type") == "say"]
    summary = next(entry["text"] for entry in say_entries if "Transfers Complete" in entry["text"])
    assert "✓ ₦10,000 → Mum (Mercy Johnson) • Opay • 8162511023" in summary
    assert "✓ ₦10,000 → Tolu (Grace Ngozi Adebayo) • Access Bank • 0762511023" in summary


async def test_schedule_management_task_bypasses_transfer_mandate_gate() -> None:
    worker = _MockScheduleCountWorker()
    state = OrchestratorState(
        user_id="u_schedule_count",
        phone_number="2348000000117",
        channel="telegram",
        last_message_text="How many scheduled transaction is pending",
        tasks={
            "schedule_count": TaskSpec(
                id="schedule_count",
                type="schedule",
                stage=TaskStage.DRAFT,
                payload={
                    "action": "list_scheduled_transactions",
                    "schedule_response_mode": "count",
                    "instruction": "How many scheduled transaction is pending",
                },
            )
        },
        waves=[["schedule_count"]],
        current_wave_index=0,
        loaded_context={
            "language": "en",
            "user_id": "user-1",
            "accounts": [
                {
                    "id": "acct-pending",
                    "bank_name": "Access Bank",
                    "account_number": "0000000003",
                    "mandate_status": "pending",
                }
            ],
        },
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert worker.call_count == 1
    assert worker.last_payload and worker.last_payload["schedule_response_mode"] == "count"
    assert updates["tasks"]["schedule_count"].stage == TaskStage.COMPLETED
    assert updates["current_wave_index"] == 1
    assert updates["outbox"] == [{"type": "say", "text": "Pending scheduled transactions: 2."}]
    assert updates["context_frames"][-1].frame_type == ContextFrameType.SCHEDULE_LIST
    assert updates["context_frames"][-1].items[0].data["target"] == "Mum"


def test_schedule_context_frame_followup_can_show_count_items() -> None:
    frame = ContextFrame(
        frame_id="schedule_list_1",
        frame_type=ContextFrameType.SCHEDULE_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.GENERIC,
                entity_id="sch-transfer",
                label="Transfer: ₦5,000 Mum • One Time at 8:00 AM Lagos time",
                data={
                    "type": "scheduled_transaction",
                    "schedule_id": "sch-transfer",
                    "domain": "Transfer",
                    "amount": "₦5,000",
                    "target": "Mum",
                    "recurrence": "One Time",
                    "schedule_time": "8:00 AM Lagos time",
                    "status": "active",
                },
            )
        ],
        created_at_ts=int(time.time()),
    )
    state = OrchestratorState(
        user_id="u_schedule_show",
        phone_number="2348000000119",
        context_frames=[frame],
        loaded_context={"language": "en"},
    )

    response = build_context_frame_followup_response(
        state,
        "show me",
        decision=ContextFrameFollowupDecision(decision="show_details", confidence=0.9),
    )

    assert response is not None
    assert response.response is not None
    assert "Scheduled Transaction Details" in response.response
    assert "Mum" in response.response
    assert "Target:" not in response.response
    assert "Schedule Id" not in response.response
    assert "sch-transfer" not in response.response
    assert response.recent_domain_focus == "schedule"


async def test_advance_wave_fails_stalled_schedule_task_instead_of_self_looping() -> None:
    state = OrchestratorState(
        user_id="u_schedule_stall",
        phone_number="2348000000118",
        channel="telegram",
        last_message_text="How many scheduled transaction is pending",
        tasks={
            "schedule_count": TaskSpec(
                id="schedule_count",
                type="schedule",
                stage=TaskStage.DRAFT,
                payload={
                    "action": "list_scheduled_transactions",
                    "schedule_response_mode": "count",
                },
            )
        },
        waves=[["schedule_count"]],
        current_wave_index=0,
        loaded_context={"language": "en", "user_id": "user-1", "accounts": []},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": _MockStallingScheduleWorker()}}}

    updates = await advance_wave(state, config)

    task = updates["tasks"]["schedule_count"]
    assert task.stage == TaskStage.FAILED
    assert task.payload["error"] == "task made no terminal or blocking progress"
    assert updates["current_wave_index"] == 1
