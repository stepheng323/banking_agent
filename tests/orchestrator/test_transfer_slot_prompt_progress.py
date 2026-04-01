"""Transfer slot-filling prompt progression tests."""

import time

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.finalize import finalize
from shared.config.settings import settings


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
    assert "what's recipient's account number and bank?" in text


async def test_transfer_handler_passes_recent_beneficiary_context_to_worker() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state()
    now = int(time.time())
    state.context_frames = [
        ContextFrame(
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
    ]
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.last_context is not None
    assert worker.last_context.get("recent_beneficiary_context") is True


async def test_transfer_handler_passes_focused_previous_beneficiary_to_worker() -> None:
    worker = _MockTransferNeedsInputWorker(["recipient_account", "recipient_bank_name"])
    state = _build_state()
    now = int(time.time())
    state.context_frames = [
        ContextFrame(
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
                ),
                ContextEntity(
                    entity_type=EntityType.BENEFICIARY,
                    entity_id="bene-2",
                    label="Dad",
                    data={
                        "id": "bene-2",
                        "alias": "Dad",
                        "account_name": "Papa Johnson",
                        "account_number": "2010000003",
                        "bank_name": "GTBank",
                        "bank_code": "058",
                    },
                ),
            ],
            focus_index=0,
            created_at_ts=now,
            ttl_seconds=600,
        )
    ]
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    await advance_wave(state, config)

    assert worker.last_context is not None
    assert worker.last_context.get("previous_beneficiary") == {
        "id": "bene-1",
        "alias": "Mum",
        "account_name": "Mercy Johnson",
        "account_number": "8162511023",
        "bank_name": "Opay",
        "bank_code": "100004",
    }


async def test_non_transfer_task_uses_worker_prompt_not_transfer_formatter() -> None:
    """Airtime/data tasks with non-transfer missing fields should use the worker's own prompt."""
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

    assert text == airtime_prompt
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
