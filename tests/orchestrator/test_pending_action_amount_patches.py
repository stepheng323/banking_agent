import pytest

from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.patches.pending_action_amount_patches import (
    _typed_amount_patch,
)
from shared.types.amount_mutation import AmountMutation, AmountMutationStep


def test_pending_transfer_amount_increment_is_applied_against_the_current_amount() -> None:
    patch = _typed_amount_patch(
        AmountMutation(steps=[AmountMutationStep(operation="add", amount=5000)]),
        task_type="transfer",
        current_amount=10000,
    )

    assert patch["amount"] == 15000
    assert patch["confirmation"] == {"confirmed": False}
    assert patch["funding_plan"] is None


def test_pending_transfer_amount_decrement_cannot_make_the_transfer_non_positive() -> None:
    patch = _typed_amount_patch(
        AmountMutation(steps=[AmountMutationStep(operation="subtract", amount=10000)]),
        task_type="transfer",
        current_amount=10000,
    )

    assert patch == {}


def test_pending_amount_set_mutation_remains_a_replacement() -> None:
    patch = _typed_amount_patch(
        AmountMutation(steps=[AmountMutationStep(operation="set", amount=5000)]),
        task_type="transfer",
        current_amount=10000,
    )

    assert patch["amount"] == 5000


def test_pending_amount_multiplier_and_compound_mutation_are_applied_in_order() -> None:
    patch = _typed_amount_patch(
        AmountMutation(
            steps=[
                AmountMutationStep(operation="multiply", factor=2),
                AmountMutationStep(operation="add", amount=5000),
            ]
        ),
        task_type="transfer",
        current_amount=10000,
    )

    assert patch["amount"] == 25000


def test_pending_relative_amount_mutation_requires_a_current_amount() -> None:
    patch = _typed_amount_patch(
        AmountMutation(steps=[AmountMutationStep(operation="multiply", factor=2)]),
        task_type="transfer",
    )

    assert patch == {}


def test_amount_mutation_schema_rejects_malformed_operands() -> None:
    with pytest.raises(ValueError, match="multiply requires"):
        AmountMutation(steps=[AmountMutationStep(operation="multiply", amount=5000)])

    with pytest.raises(ValueError, match="add requires"):
        AmountMutation(steps=[AmountMutationStep(operation="add", factor=2)])
