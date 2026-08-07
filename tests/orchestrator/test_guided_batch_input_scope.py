from apps.chat.src.agent.orchestrator.models.domain import (
    BatchInputContract,
    BatchInputSlot,
    PendingInterrupt,
    TaskSpec,
    TaskStage,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts_focused import (
    _build_focused_missing_field_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts_guided import (
    build_guided_batch_input_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_finalize import (
    _attach_partial_batch_failure_notice,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.batch_input_scope import (
    resolve_batch_input_message_scope,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates


def _state() -> tuple[OrchestratorState, PendingInterrupt]:
    transfer = TaskSpec(
        id="transfer_1",
        type="transfer",
        payload={
            "amount": 2_000,
            "recipient_name": "Tolu",
            "beneficiary_candidates": [
                {"beneficiary_id": "bene_gtb", "label": "Tolu GTB Tolu Adeyemi"},
                {"beneficiary_id": "bene_access", "label": "Tolu Access Tolu Adebayo"},
            ],
        },
    )
    airtime = TaskSpec(
        id="airtime_1",
        type="airtime",
        payload={"amount": None, "recipient_phone": "08162511023", "network": "MTN"},
    )
    contract = BatchInputContract(
        slots=[
            BatchInputSlot(task_id="transfer_1", field="beneficiary_id", kind="selection"),
            BatchInputSlot(task_id="airtime_1", field="amount", kind="amount"),
        ],
        focused_slot=BatchInputSlot(task_id="transfer_1", field="beneficiary_id", kind="selection"),
    )
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=["transfer_1", "airtime_1"],
        fields_by_task={"transfer_1": ["beneficiary_id"], "airtime_1": ["amount"]},
        batch_input=contract,
    )
    return OrchestratorState(
        user_id="u_guided_batch", phone_number="2348000000000", tasks={transfer.id: transfer, airtime.id: airtime}
    ), interrupt


def test_selection_scope_never_targets_airtime_amount() -> None:
    state, interrupt = _state()
    assert resolve_batch_input_message_scope(state=state, interrupt=interrupt, text="1") == {"transfer_1": "1"}


def test_partial_failure_is_folded_into_one_selection_card() -> None:
    state, interrupt = _state()
    state.tasks["airtime_1"].payload["_batch_failure_notice"] = (
        "Failed: I couldn't find an account matching 'Access Bank'.\n\n"
        "One item in this batch is still waiting for your input. The failed item was not submitted."
    )
    updates = _attach_partial_batch_failure_notice(
        state=state,
        current_wave=["transfer_1", "airtime_1"],
        updates={
            "pending_interrupt": interrupt,
            "outbox": [
                {
                    "type": "show_options",
                    "title": "Who should receive the transfer?",
                    "task_ids": ["transfer_1", "airtime_1"],
                    "options": [{"id": "bene_gtb", "title": "Tolu GTB"}],
                }
            ],
        },
    )

    assert [entry["type"] for entry in updates["outbox"]] == ["show_options"]
    card = updates["outbox"][0]
    assert "Failed: I couldn't find an account matching 'Access Bank'." in card["title"]
    assert "Who should receive the transfer?" in card["title"]
    assert card["_batch_status"] == "partial_failure"


def test_amount_scope_never_targets_beneficiary_selection() -> None:
    state, interrupt = _state()
    assert resolve_batch_input_message_scope(state=state, interrupt=interrupt, text="2k") == {"airtime_1": "2k"}


def test_combined_selection_and_amount_scopes_each_task() -> None:
    state, interrupt = _state()
    assert resolve_batch_input_message_scope(state=state, interrupt=interrupt, text="Tolu GTB and 2k") == {
        "transfer_1": "Tolu GTB",
        "airtime_1": "2k",
    }


def test_selection_replay_is_suppressed_for_unattributed_batch_sibling() -> None:
    state, interrupt = _state()
    updates = _continue_flow_updates(
        state,
        interrupt,
        input_messages_by_task={"transfer_1": "1"},
    )
    transfer = updates["tasks"]["transfer_1"]
    airtime = updates["tasks"]["airtime_1"]
    assert transfer.payload["pending_user_message"] == "1"
    assert "suppress_current_input" not in transfer.payload
    assert airtime.payload["suppress_current_input"] is True
    assert "pending_user_message" not in airtime.payload


def test_selection_resume_preserves_typed_self_destination() -> None:
    state, interrupt = _state()
    state.tasks["airtime_1"] = TaskSpec(
        id="airtime_1",
        type="transfer",
        payload={
            "amount": 5_000,
            "is_self": True,
            "recipient_bank_name": "Access Bank",
            "recipient_name": "Tolu Adebayo",
            "recipient_account": "2010000002",
            "recipient_resolved_name": "Tolu Adebayo",
        },
    )

    updates = _continue_flow_updates(
        state,
        interrupt,
        input_messages_by_task={"transfer_1": "1"},
    )

    self_payload = updates["tasks"]["airtime_1"].payload
    assert self_payload["is_self"] is True
    assert self_payload["recipient_bank_name"] == "Access Bank"
    assert "recipient_name" not in self_payload
    assert "recipient_account" not in self_payload
    assert "recipient_resolved_name" not in self_payload


def test_stale_selection_ack_is_not_combined_with_current_options() -> None:
    state, interrupt = _state()
    state.last_interrupt = interrupt
    state.tasks["transfer_1"].payload.update(
        {
            "beneficiary_id": "bene_gtb",
            "recipient_resolved_name": "Tolu Adeyemi",
        }
    )
    state.tasks["transfer_2"] = TaskSpec(
        id="transfer_2",
        type="transfer",
        payload={"amount": 5_000, "recipient_name": "my Access account"},
    )

    accumulator = ExecutionAccumulator(state.tasks)
    accumulator.add_missing_fields("transfer_1", ["beneficiary_id"])
    accumulator.add_missing_fields("transfer_2", ["recipient_account", "recipient_bank_name"])
    accumulator.add_prompt(
        "I found multiple matches for 'Tolu Adebayo'. Which one did you mean?",
        task_id="transfer_1",
    )
    accumulator.add_details(
        "transfer_1",
        {
            "candidates": [
                {"beneficiary_id": "bene_gtb", "label": "Tolu GTB"},
                {"beneficiary_id": "bene_access", "label": "Tolu Access"},
            ]
        },
    )

    updates = build_guided_batch_input_updates(
        state=state,
        current_wave=["transfer_1", "transfer_2"],
        agg=accumulator,
        locale="en",
    )

    title = updates["outbox"][0]["title"]
    assert "Choose the recipient for ₦2,000 to “Tolu”." in title
    assert "Done." not in title
    assert "Still needed: the account details." in title


def test_selection_ack_is_shown_only_after_selection_slot_is_resolved() -> None:
    state, interrupt = _state()
    state.last_interrupt = interrupt
    state.tasks["transfer_1"].payload.update(
        {
            "beneficiary_id": "bene_gtb",
            "recipient_resolved_name": "Tolu Adeyemi",
        }
    )

    accumulator = ExecutionAccumulator(state.tasks)
    accumulator.add_missing_fields("airtime_1", ["amount"])
    accumulator.add_prompt("How much airtime should I buy?", task_id="airtime_1")

    updates = build_guided_batch_input_updates(
        state=state,
        current_wave=["transfer_1", "airtime_1"],
        agg=accumulator,
        locale="en",
    )

    text = updates["outbox"][0]["text"]
    assert "Great — ₦2,000 will go to Tolu (Tolu Adeyemi)." in text
    assert "Next up: How much airtime should I buy?" in text


def test_recipient_selection_does_not_show_review_ready_sibling_as_queued() -> None:
    state, _interrupt = _state()
    state.tasks["airtime_1"] = TaskSpec(
        id="airtime_1",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={
            "amount": 5000,
            "is_self": True,
            "recipient_bank_name": "Access Bank",
            "confirmation": {
                "summary": "Confirm linked-account transfer",
                "snapshot": {"amount": 5000},
            },
        },
    )
    accumulator = ExecutionAccumulator(state.tasks)
    accumulator.add_missing_fields("transfer_1", ["beneficiary_id"])
    accumulator.add_prompt("I found multiple matches for 'Tolu'. Which one did you mean?", "transfer_1")
    accumulator.add_details(
        "transfer_1",
        {"options": [{"id": "bene_gtb", "title": "Tolu GTB"}]},
    )

    updates = _build_focused_missing_field_updates(
        state=state,
        current_wave=["transfer_1", "airtime_1"],
        agg=accumulator,
        locale="en",
        focused_tid="transfer_1",
    )

    title = str(updates["outbox"][0].get("title") or updates["outbox"][0].get("text") or "")
    assert "Also in this batch" not in title
    assert "Confirm linked-account transfer" not in title
    assert "Choose the recipient for ₦2,000 to “Tolu”." in title
    assert "your Access Bank account" not in title
    assert "Other items in this batch are ready" not in title
    assert "one combined review" not in title


def test_recipient_selection_uses_typed_lead_when_worker_omits_name() -> None:
    state, _interrupt = _state()
    state.tasks["transfer_1"].payload.pop("recipient_name")
    accumulator = ExecutionAccumulator(state.tasks)
    accumulator.add_missing_fields("transfer_1", ["beneficiary_id"])
    accumulator.add_prompt("I found multiple matches. Which one did you mean?", "transfer_1")
    accumulator.add_details(
        "transfer_1",
        {"options": [{"id": "bene_gtb", "title": "Tolu GTB · GTBank"}]},
    )

    updates = _build_focused_missing_field_updates(
        state=state,
        current_wave=["transfer_1", "airtime_1"],
        agg=accumulator,
        locale="en",
        focused_tid="transfer_1",
    )

    title = str(updates["outbox"][0].get("title") or "")
    assert "Choose a saved recipient for ₦2,000." in title
    assert "Which one did you mean?" not in title
