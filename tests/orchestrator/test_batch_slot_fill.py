from decimal import Decimal

import pytest

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from banking.transactions.shared.confirmation.models import ConfirmationDecision
from shared.types.planner import BatchSlotPatchDecision, BatchSlotPatchUpdate


class _BatchSlotPlanner:
    def __init__(
        self,
        decision: BatchSlotPatchDecision | None = None,
        confirmation_decision: ConfirmationDecision | None = None,
    ) -> None:
        self.decision = decision
        self.confirmation_decision = confirmation_decision
        self.batch_slot_calls = 0
        self.confirmation_calls = 0
        self.route_calls = 0

    async def interpret_batch_slot_patch(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> BatchSlotPatchDecision:
        del phone_number, text, context, path_label
        self.batch_slot_calls += 1
        assert self.decision is not None
        return self.decision

    async def classify_confirmation_reply(self, *args: object, **kwargs: object) -> ConfirmationDecision:
        del args, kwargs
        self.confirmation_calls += 1
        assert self.confirmation_decision is not None
        return self.confirmation_decision

    async def route_pending_input(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.route_calls += 1
        raise AssertionError("route_pending_input should not be called for batch slot patches")


def _batch_state(*, text: str, task_ids: list[str] | None = None) -> OrchestratorState:
    return OrchestratorState(
        user_id="u_batch_slot",
        phone_number="2348000000100",
        channel="whatsapp",
        last_message_text=text,
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=task_ids or ["t_mom"],
            fields_by_task={"t_mom": ["recipient_account", "recipient_bank_name"]},
            prompt="Please share the account number and bank for your mom.",
        ),
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 40000, "recipient_name": "mom", "source_affinity_mode": "auto"},
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 30000, "recipient_name": "ay", "source_affinity_mode": "auto"},
            ),
        },
        loaded_context={"language": "en"},
    )


@pytest.mark.asyncio
async def test_batch_slot_fastpath_applies_labelled_details_to_focused_and_sibling_tasks() -> None:
    state = _batch_state(text="8067892221, wema for mum and 808 084 4362, opay for ayo")

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    tasks = updates["tasks"]
    assert tasks["t_mom"].payload["recipient_account"] == "8067892221"
    assert tasks["t_mom"].payload["recipient_bank_name"] == "Wema"
    assert tasks["t_ay"].payload["recipient_account"] == "8080844362"
    assert tasks["t_ay"].payload["recipient_bank_name"] == "Opay"
    assert tasks["t_mom"].payload["funding_plan"] is None
    assert tasks["t_ay"].payload["suggested_funding_plan"] is None


@pytest.mark.asyncio
async def test_batch_slot_fastpath_single_detail_only_patches_focused_task() -> None:
    state = _batch_state(text="8067892221, wema")

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    tasks = updates["tasks"]
    assert tasks["t_mom"].payload["recipient_account"] == "8067892221"
    assert tasks["t_mom"].payload["recipient_bank_name"] == "Wema"
    assert "recipient_account" not in tasks["t_ay"].payload


@pytest.mark.asyncio
async def test_batch_slot_invalid_account_bank_detail_does_not_apply_partial_patch() -> None:
    state = _batch_state(text="8067892221, wema for mum and 8080844362")

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "recipient_account" not in updates["tasks"]["t_mom"].payload
    assert "recipient_account" not in updates["tasks"]["t_ay"].payload
    assert "only the bank name" in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_batch_slot_fastpath_applies_terse_pair_amounts_to_batch_tasks() -> None:
    state = _batch_state(text="Make 30 30", task_ids=["t_mom", "t_ay"])

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    tasks = updates["tasks"]
    assert Decimal(str(tasks["t_mom"].payload["amount"])) == Decimal("30000")
    assert Decimal(str(tasks["t_ay"].payload["amount"])) == Decimal("30000")
    assert tasks["t_mom"].payload["funding_plan"] is None
    assert tasks["t_ay"].payload["suggested_funding_plan"] is None


@pytest.mark.asyncio
async def test_batch_source_choice_fastpath_applies_default_and_selected_source_to_batch() -> None:
    state = _batch_state(text="First Bank", task_ids=["t_mom", "t_ay"])
    state.tasks["t_mom"].payload.update(
        {
            "recipient_account": "8067892221",
            "recipient_bank_name": "Wema",
            "recipient_resolved_name": "FATIMA ZAHRA MUSA",
            "recipient_resolution_provider": "mono",
            "recipient_resolution_mode": "single_source",
            "recipient_review_confirmed": True,
            "recipient_review_signature": "existing-mom-review",
        }
    )
    state.tasks["t_ay"].payload.update(
        {
            "recipient_account": "7750145200",
            "recipient_bank_name": "Opay",
            "recipient_resolved_name": "EMMANUEL TUNDE BAKARE",
            "recipient_resolution_provider": "mono",
            "recipient_resolution_mode": "single_source",
            "recipient_review_confirmed": True,
            "recipient_review_signature": "existing-ay-review",
        }
    )
    state.pending_interrupt = PendingInterrupt(
        kind="input",
        task_ids=["t_mom", "t_ay"],
        fields_by_task={"t_mom": ["source_accounts", "funding_plan"], "t_ay": ["source_accounts", "funding_plan"]},
        prompt=(
            "Funding review\n\n"
            "This batch needs ₦60,000.\n"
            "Your default Access Bank has ₦30,000.\n"
            "I need one more account for the remaining ₦30,000.\n\n"
            "First Bank (···0001) and GTBank (···0002) can cover it.\n"
            "Which account should I use?"
        ),
    )
    state.loaded_context["transaction_accounts"] = [
        {"id": "acc_access", "bank_name": "Access Bank", "account_number": "0000000003", "is_default": True},
        {"id": "acc_first", "bank_name": "First Bank", "account_number": "0000000001", "is_default": False},
        {"id": "acc_gtb", "bank_name": "GTBank", "account_number": "0000000002", "is_default": False},
    ]

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    for task_id in ["t_mom", "t_ay"]:
        payload = updates["tasks"][task_id].payload
        assert payload["source_accounts"] == ["Access Bank", "First Bank"]
        assert payload["use_dual_accounts"] is True
        assert payload["source_affinity_mode"] == "explicit"
        assert payload["funding_plan"] is None
        assert payload["suggested_funding_plan"] is None
        assert payload["recipient_resolution_provider"] == "mono"
        assert payload["recipient_resolution_mode"] == "single_source"
        assert payload["recipient_review_confirmed"] is True


@pytest.mark.asyncio
async def test_batch_source_choice_fastpath_understands_use_gtb_as_anchor_plus_gtbank() -> None:
    state = _batch_state(text="Use gtb", task_ids=["t_mom", "t_ay"])
    state.pending_interrupt = PendingInterrupt(
        kind="input",
        task_ids=["t_mom", "t_ay"],
        fields_by_task={"t_mom": ["source_accounts", "funding_plan"], "t_ay": ["source_accounts", "funding_plan"]},
        prompt=(
            "Funding review\n\n"
            "Total needed: ₦60,000.\n"
            "Your default Access Bank has ₦30,000.\n"
            "To cover the remaining ₦30,000, choose one more source:\n"
            "• First Bank (···0001): ₦30,000\n"
            "• GTBank (···0002): ₦30,000\n\n"
            "Reply with the bank you want to use."
        ),
    )
    state.loaded_context["transaction_accounts"] = [
        {"id": "acc_access", "bank_name": "Access Bank", "account_number": "0000000003", "is_default": True},
        {"id": "acc_first", "bank_name": "First Bank", "account_number": "0000000001", "is_default": False},
        {"id": "acc_gtb", "bank_name": "GTBank", "account_number": "0000000002", "is_default": False},
    ]

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    for task_id in ["t_mom", "t_ay"]:
        payload = updates["tasks"][task_id].payload
        assert payload["source_accounts"] == ["Access Bank", "GTBank"]
        assert payload["use_dual_accounts"] is True
        assert payload["source_affinity_mode"] == "explicit"


@pytest.mark.asyncio
async def test_batch_source_choice_fastpath_uses_selected_anchor_metadata() -> None:
    state = _batch_state(text="Use gtb", task_ids=["t_mom", "t_ay"])
    state.pending_interrupt = PendingInterrupt(
        kind="input",
        task_ids=["t_mom", "t_ay"],
        fields_by_task={"t_mom": ["source_accounts", "funding_plan"], "t_ay": ["source_accounts", "funding_plan"]},
        prompt=(
            "Funding review\n\n"
            "Total needed: ₦60,000.\n"
            "Your selected First Bank has ₦30,000.\n"
            "To cover the remaining ₦30,000, choose one more source:\n"
            "• Access Bank (···0003): ₦30,000\n"
            "• GTBank (···0002): ₦30,000\n\n"
            "Reply with the bank you want to use."
        ),
        metadata={"anchor_source_ids": ["acc_first"], "candidate_source_ids": ["acc_access", "acc_gtb"]},
    )
    state.loaded_context["transaction_accounts"] = [
        {"id": "acc_access", "bank_name": "Access Bank", "account_number": "0000000003", "is_default": True},
        {"id": "acc_first", "bank_name": "First Bank", "account_number": "0000000001", "is_default": False},
        {"id": "acc_gtb", "bank_name": "GTBank", "account_number": "0000000002", "is_default": False},
    ]

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    for task_id in ["t_mom", "t_ay"]:
        payload = updates["tasks"][task_id].payload
        assert payload["source_accounts"] == ["First Bank", "GTBank"]
        assert payload["use_dual_accounts"] is True
        assert payload["source_pooling_locked"] is False
        assert payload["source_affinity_mode"] == "explicit"


@pytest.mark.asyncio
async def test_batch_source_choice_fastpath_only_gtb_replaces_default_anchor() -> None:
    state = _batch_state(text="Only gtb", task_ids=["t_mom", "t_ay"])
    state.pending_interrupt = PendingInterrupt(
        kind="input",
        task_ids=["t_mom", "t_ay"],
        fields_by_task={"t_mom": ["source_accounts", "funding_plan"], "t_ay": ["source_accounts", "funding_plan"]},
        prompt=(
            "Funding review\n\n"
            "Total needed: ₦60,000.\n"
            "Your default Access Bank has ₦30,000.\n"
            "Reply with the bank you want to use."
        ),
    )
    state.loaded_context["transaction_accounts"] = [
        {"id": "acc_access", "bank_name": "Access Bank", "account_number": "0000000003", "is_default": True},
        {"id": "acc_gtb", "bank_name": "GTBank", "account_number": "0000000002", "is_default": False},
    ]

    updates = await handle_pending_interrupt(state, {"configurable": {}, "recursion_limit": 50})

    assert updates["pending_interrupt"] is None
    for task_id in ["t_mom", "t_ay"]:
        payload = updates["tasks"][task_id].payload
        assert payload["source_accounts"] == ["GTBank"]
        assert payload["use_dual_accounts"] is False
        assert payload["source_pooling_locked"] is True
        assert payload["source_affinity_mode"] == "explicit"


@pytest.mark.asyncio
async def test_batch_slot_semantic_fallback_applies_validated_amount_update() -> None:
    state = _batch_state(text="reduce ay si 20k")
    planner = _BatchSlotPlanner(
        BatchSlotPatchDecision(
            confidence=0.91,
            detected_language="Yoruba",
            updates=[
                BatchSlotPatchUpdate(
                    target_texts=["ay"],
                    amount=20000,
                )
            ],
            reason="semantic batch amount edit",
        )
    )

    updates = await handle_pending_interrupt(
        state,
        {"configurable": {"task_planner": planner, "semantic_router_llm": planner, "capability_classifier_llm": planner}, "recursion_limit": 50},
    )

    assert planner.batch_slot_calls == 1
    assert planner.route_calls == 0
    assert updates["pending_interrupt"] is None
    assert updates["tasks"]["t_ay"].payload["amount"] == 20000
    assert updates["tasks"]["t_ay"].payload["funding_plan"] is None


@pytest.mark.asyncio
async def test_batch_slot_semantic_patch_rejects_cross_clause_bank_text() -> None:
    state = _batch_state(text="change mum details")
    planner = _BatchSlotPlanner(
        BatchSlotPatchDecision(
            confidence=0.93,
            detected_language="English",
            updates=[
                BatchSlotPatchUpdate(
                    target_texts=["mum"],
                    recipient_account="8067892221",
                    recipient_bank_name="wema for mum and 808 084 4362, opay for ayo",
                )
            ],
            reason="malformed semantic bank",
        )
    )

    updates = await handle_pending_interrupt(
        state,
        {"configurable": {"task_planner": planner, "semantic_router_llm": planner, "capability_classifier_llm": planner}, "recursion_limit": 50},
    )

    assert planner.batch_slot_calls == 1
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert "recipient_account" not in updates["tasks"]["t_mom"].payload
    assert "only the bank name" in updates["outbox"][0]["text"]


@pytest.mark.asyncio
async def test_batch_slot_semantic_clarification_keeps_interrupt_open() -> None:
    state = _batch_state(text="reduce them")
    planner = _BatchSlotPlanner(
        BatchSlotPatchDecision(
            confidence=0.88,
            detected_language="English",
            needs_clarification=True,
            clarification="Which transfer should I reduce: mom or ay?",
            reason="ambiguous target",
        )
    )

    updates = await handle_pending_interrupt(
        state,
        {"configurable": {"task_planner": planner, "semantic_router_llm": planner, "capability_classifier_llm": planner}, "recursion_limit": 50},
    )

    assert planner.batch_slot_calls == 1
    assert updates["pending_interrupt"] == state.pending_interrupt
    assert updates["outbox"][0]["text"] == "Which transfer should I reduce: mom or ay?"


@pytest.mark.asyncio
async def test_recipient_review_uses_semantic_confirmation_fallback_for_natural_approval() -> None:
    state = _batch_state(text="Very well", task_ids=["t_mom", "t_ay"])
    state.pending_interrupt = PendingInterrupt(
        kind="input",
        task_ids=["t_mom", "t_ay"],
        fields_by_task={"t_mom": ["recipient_review_confirmed"], "t_ay": ["recipient_review_confirmed"]},
        prompt=(
            "Recipient review\n\n"
            "I found these recipients:\n\n"
            "Are these correct? Reply yes to continue, or tell me what to change."
        ),
    )
    for task_id, resolved_name, bank, account in (
        ("t_mom", "FATIMA ZAHRA MUSA", "Wema", "8067892221"),
        ("t_ay", "YUSUF IBRAHIM", "Opay", "8080844362"),
    ):
        state.tasks[task_id].stage = TaskStage.AWAITING_CONFIRMATION
        state.tasks[task_id].payload.update(
            {
                "recipient_resolved_name": resolved_name,
                "recipient_bank_name": bank,
                "recipient_account": account,
                "recipient_resolution_provider": "mono",
                "recipient_resolution_mode": "single_source",
                "recipient_review_required": True,
                "recipient_review_confirmed": False,
            }
        )
    planner = _BatchSlotPlanner(
        confirmation_decision=ConfirmationDecision("approve", "llm", 0.94, "natural approval")
    )

    updates = await handle_pending_interrupt(
        state,
        {"configurable": {"task_planner": planner, "semantic_router_llm": planner, "capability_classifier_llm": planner}, "recursion_limit": 50},
    )

    assert planner.confirmation_calls == 1
    assert updates["pending_interrupt"] is None
    for task_id in ("t_mom", "t_ay"):
        payload = updates["tasks"][task_id].payload
        assert payload["recipient_review_confirmed"] is True
        assert payload["recipient_review_required"] is False
        assert payload["recipient_review_signature"]


@pytest.mark.asyncio
async def test_recipient_review_exact_yes_does_not_call_semantic_confirmation() -> None:
    state = _batch_state(text="Yes", task_ids=["t_mom"])
    state.pending_interrupt = PendingInterrupt(
        kind="input",
        task_ids=["t_mom"],
        fields_by_task={"t_mom": ["recipient_review_confirmed"]},
        prompt="Are these correct? Reply yes to continue, or tell me what to change.",
    )
    state.tasks["t_mom"].stage = TaskStage.AWAITING_CONFIRMATION
    state.tasks["t_mom"].payload.update(
        {
            "recipient_resolved_name": "FATIMA ZAHRA MUSA",
            "recipient_bank_name": "Wema",
            "recipient_account": "8067892221",
            "recipient_resolution_provider": "mono",
            "recipient_resolution_mode": "single_source",
            "recipient_review_required": True,
            "recipient_review_confirmed": False,
        }
    )
    planner = _BatchSlotPlanner(
        confirmation_decision=ConfirmationDecision("approve", "llm", 0.94, "should not be used")
    )

    updates = await handle_pending_interrupt(
        state,
        {"configurable": {"task_planner": planner, "semantic_router_llm": planner, "capability_classifier_llm": planner}, "recursion_limit": 50},
    )

    assert planner.confirmation_calls == 0
    assert updates["tasks"]["t_mom"].payload["recipient_review_confirmed"] is True
