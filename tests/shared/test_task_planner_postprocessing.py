import pytest

from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_build import (
    _build_planner_task_updates,
)
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_postprocessing import (
    _postprocess_planner_tasks,
)
from shared.types.planner import (
    AirtimeTaskParameters,
    PlannerClause,
    PlannerOutput,
    TransferTaskParameters,
    make_planned_task,
)


def test_transfer_fanout_handles_alias_list_when_planner_misparses_bank_name() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 2k each to Tolu Access and Tolu GTB",
                parameters=TransferTaskParameters(amount=2000, bank_name="GTBank"),
                risk="MONEY_MOVE",
            )
        ],
        confidence=0.98,
        detected_language="English",
    )

    postprocessed = _postprocess_planner_tasks(
        planner_output,
        "Send 2k each to Tolu Access and Tolu GTB",
    )

    assert [task.task_id for task in postprocessed.tasks] == ["t1", "t1_r2"]
    assert [task.parameters.recipient_name for task in postprocessed.tasks] == ["tolu access", "tolu gtb"]
    assert [task.parameters.recipient_binding_index for task in postprocessed.tasks] == [1, 2]
    assert all(task.parameters.recipient_binding_source == "fanout" for task in postprocessed.tasks)
    assert all(task.parameters.amount == 2000 for task in postprocessed.tasks)
    assert all(task.parameters.bank_name is None for task in postprocessed.tasks)


def test_transfer_fanout_uses_user_text_when_clause_text_drops_alias() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        clauses=[
            PlannerClause(
                clause_index=1,
                text="Send 2k each to Tolu GTB",
                intent_family="transfer",
                task_ids=["t1"],
            )
        ],
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 2k each to Tolu Access and Tolu GTB",
                source_clause_index=1,
                parameters=TransferTaskParameters(amount=2000, bank_name="GTBank"),
                risk="MONEY_MOVE",
            )
        ],
        confidence=0.98,
        detected_language="English",
    )

    postprocessed = _postprocess_planner_tasks(
        planner_output,
        "Send 2k each to Tolu Access and Tolu GTB",
    )

    assert [task.task_id for task in postprocessed.tasks] == ["t1", "t1_r2"]
    assert [task.parameters.recipient_name for task in postprocessed.tasks] == ["tolu access", "tolu gtb"]
    assert all(task.parameters.bank_name is None for task in postprocessed.tasks)


def test_transfer_fanout_keeps_single_recipient_bank_task_unchanged() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 2k to Tolu GTB",
                parameters=TransferTaskParameters(amount=2000, bank_name="GTBank"),
                risk="MONEY_MOVE",
            )
        ],
        confidence=0.98,
        detected_language="English",
    )

    postprocessed = _postprocess_planner_tasks(planner_output, "Send 2k to Tolu GTB")

    assert len(postprocessed.tasks) == 1
    assert postprocessed.tasks[0].task_id == "t1"
    assert postprocessed.tasks[0].parameters.bank_name == "GTBank"
    assert postprocessed.tasks[0].parameters.recipient_name is None


def test_self_transfer_leg_is_not_reconciled_with_external_recipient_text() -> None:
    """A typed own-account leg must remain independent of its sibling."""
    planner_output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t_external",
                action="send_money",
                executor="transfer",
                instruction="Send 2k to Tolu Adebayo",
                parameters=TransferTaskParameters(amount=2000, recipient_name="Tolu Adebayo"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t_self",
                action="send_money",
                executor="transfer",
                instruction="Send 5k to my Access account",
                parameters=TransferTaskParameters(
                    amount=5000,
                    bank_name="Access Bank",
                    recipient_name="Tolu Adebayo",
                    is_self=True,
                ),
                risk="MONEY_MOVE",
            ),
        ],
        confidence=0.98,
        detected_language="English",
    )

    postprocessed = _postprocess_planner_tasks(
        planner_output,
        "Send 2k to Tolu Adebayo and 5k to my Access account",
    )

    self_task = next(task for task in postprocessed.tasks if task.task_id == "t_self")
    assert self_task.parameters.is_self is True
    assert self_task.parameters.recipient_name == "Tolu Adebayo"
    # The planner-level contract still carries the raw signal; task
    # materialization removes the external name before execution.
    assert self_task.parameters.bank_name == "Access Bank"


def test_clause_repair_adds_missing_transfer_task_for_mixed_transfer_airtime() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        clauses=[
            PlannerClause(
                clause_index=1,
                text="Send 10 to adebayo",
                intent_family="transfer",
                extracted_fields={"amount": "10", "recipient_name": "adebayo", "source_bank_name": "GTBank"},
            ),
            PlannerClause(
                clause_index=2,
                text="buy me 2k airtime from my gtb",
                intent_family="airtime",
                task_ids=["t_airtime"],
            ),
        ],
        tasks=[
            make_planned_task(
                task_id="t_airtime",
                action="buy_airtime",
                executor="airtime",
                instruction="buy me 2k airtime from my gtb",
                source_clause_index=2,
                parameters=AirtimeTaskParameters(amount=2000, is_self=True, source_bank_name="GTBank"),
                risk="MONEY_MOVE",
            )
        ],
        confidence=0.98,
        detected_language="English",
    )

    postprocessed = _postprocess_planner_tasks(
        planner_output,
        "Send 10 to adebayo and buy me 2k airtime from my gtb",
    )

    assert [task.executor for task in postprocessed.tasks] == ["transfer", "airtime"]
    transfer_task = postprocessed.tasks[0]
    assert transfer_task.task_id == "transfer_clause_1"
    assert transfer_task.source_clause_index == 1
    assert transfer_task.parameters.amount == 10
    assert transfer_task.parameters.recipient_name == "adebayo"
    assert transfer_task.parameters.source_bank_name == "GTBank"


@pytest.mark.asyncio
async def test_underproduced_typed_batch_plan_fails_closed_before_task_materialization() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        tasks=[
            make_planned_task(
                task_id="t_external",
                action="send_money",
                executor="transfer",
                instruction="Send 2k to Tolu Adebayo",
                parameters=TransferTaskParameters(amount=2000, recipient_name="Tolu Adebayo"),
                risk="MONEY_MOVE",
            )
        ],
        confidence=0.98,
        detected_language="English",
    )

    updates = await _build_planner_task_updates(
        planner_output=planner_output,
        text="Send 2k to Tolu Adebayo and 5k to my Access account",
        locale="en",
        query_session_source=None,
        query_session_snapshot=None,
        expected_transaction_task_count=2,
    )

    assert updates["planner_incomplete_response"] is True
    assert updates["new_tasks"] == {}
    assert updates["waves"] == []
    assert "planner.transaction_task_count_mismatch" in updates["planner_quality_report"].dirty_reasons


@pytest.mark.asyncio
async def test_duplicate_typed_batch_task_ids_fail_closed_before_partial_confirmation() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="transfer_1",
                action="send_money",
                executor="transfer",
                instruction="Send 2k to Tolu Adebayo",
                parameters=TransferTaskParameters(amount=2000, recipient_name="Tolu Adebayo"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="transfer_1",
                action="send_money",
                executor="transfer",
                instruction="Send 5k to my Access account",
                parameters=TransferTaskParameters(amount=5000, bank_name="Access Bank", is_self=True),
                risk="MONEY_MOVE",
            ),
        ],
        confidence=0.98,
        detected_language="English",
    )

    updates = await _build_planner_task_updates(
        planner_output=planner_output,
        text="Send 2k to Tolu Adebayo and 5k to my Access account",
        locale="en",
        query_session_source=None,
        query_session_snapshot=None,
        expected_transaction_task_count=2,
    )

    assert updates["planner_incomplete_response"] is True
    assert updates["new_tasks"] == {}
    assert updates["waves"] == []
    assert "planner.transaction_task_materialization_mismatch" in updates["planner_quality_report"].dirty_reasons
