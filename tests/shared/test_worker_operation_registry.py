from banking.runtime.operations import WORKER_OPERATIONS, operation_spec
from banking.runtime.progress import WORKER_PROGRESS_STAGES
from shared.types.planner import _ACTION_TASK_MODELS


def test_every_planner_action_has_one_canonical_worker_operation() -> None:
    for action, task_model in _ACTION_TASK_MODELS.items():
        parameters = (
            {"preferences_update": {"presentation_detail": "detailed"}}
            if action == "update_query_preferences"
            else None
        )
        task = task_model(
            task_id="registry-test",
            action=action,
            instruction="test",
            risk=operation_spec(task_model.executor, action).risk,
            **({"parameters": parameters} if parameters is not None else {}),
        )
        assert (task.executor, task.action) in WORKER_OPERATIONS


def test_priority_operations_have_the_expected_risk_gates() -> None:
    assert operation_spec("beneficiary", "rename_beneficiary").requires_confirmation is True
    assert operation_spec("beneficiary", "rename_beneficiary").requires_pin is False
    assert operation_spec("schedule", "pause_scheduled_transaction").requires_pin is False
    assert operation_spec("schedule", "resume_scheduled_transaction").requires_pin is True
    assert operation_spec("schedule", "list_scheduled_runs").risk == "READ_ONLY"
    assert operation_spec("support", "append_support_ticket_note").risk == "MUTATION"
    assert operation_spec("support", "close_support_ticket").requires_confirmation is True


def test_removed_worker_aliases_are_not_registered() -> None:
    removed = {
        ("beneficiary", "add_beneficiary"),
        ("beneficiary", "update_beneficiary"),
        ("schedule", "list_scheduled_transfers"),
        ("schedule", "cancel_scheduled_transfer"),
        ("account", "overall_balance"),
    }
    assert removed.isdisjoint(WORKER_OPERATIONS)


def test_progress_stages_only_target_canonical_mutations_or_authorized_purchases() -> None:
    for (executor, action), stage in WORKER_PROGRESS_STAGES.items():
        operation = operation_spec(executor, action)
        assert operation.risk in {"MUTATION", "MONEY_MOVE"}
        assert stage.stage_key.count(".") == 1

    assert ("account", "check_balance") not in WORKER_PROGRESS_STAGES
    assert ("beneficiary", "list_beneficiaries") not in WORKER_PROGRESS_STAGES
    assert ("schedule", "list_scheduled_transactions") not in WORKER_PROGRESS_STAGES
    assert ("support", "list_support_tickets") not in WORKER_PROGRESS_STAGES
    assert ("data", "data_plan_query") not in WORKER_PROGRESS_STAGES
