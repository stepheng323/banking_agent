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
        loaded_context={"language": "en", "accounts": [{"id": "acct-1", "bank_name": "Test Bank", "account_number": "0000000001", "mandate_status": "ready", "mandate_id": "m1"}]},
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
    say_intent = updates["outbox"][0]
    options_intent = updates["outbox"][1]

    assert say_intent["type"] == "say"
    assert say_intent["text"] == ambiguity_prompt
    assert "account number and bank" not in say_intent["text"]

    assert options_intent["type"] == "show_options"
    assert options_intent["task_ids"] == ["t1"]
    assert [opt["id"] for opt in options_intent["options"]] == ["1", "2"]
    assert options_intent["options"][0]["title"] == "Tolu A • Access Bank • ****1234"
