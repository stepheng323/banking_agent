"""Transfer slot-filling prompt progression tests."""

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
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

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, pin_verified
        self.last_context = context
        self.last_user_message = user_message
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=self.required_fields,
            prompt=self.prompt,
            details=self.details,
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
