from apps.chat.src.agent.orchestrator.models.domain import (
    BatchInputContract,
    BatchInputSlot,
    PendingInterrupt,
    TaskSpec,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
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
    return OrchestratorState(user_id="u_guided_batch", phone_number="2348000000000", tasks={transfer.id: transfer, airtime.id: airtime}), interrupt


def test_selection_scope_never_targets_airtime_amount() -> None:
    state, interrupt = _state()
    assert resolve_batch_input_message_scope(state=state, interrupt=interrupt, text="1") == {"transfer_1": "1"}


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
