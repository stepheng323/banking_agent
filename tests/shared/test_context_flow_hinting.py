from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_hinting import (
    _has_transaction_intent_hint,
)


def test_bank_name_containing_pay_is_not_a_transaction_hint() -> None:
    assert _has_transaction_intent_hint("Have I linked my OPay account?") is False


def test_standalone_payment_action_remains_a_transaction_hint() -> None:
    assert _has_transaction_intent_hint("Pay Mum 5k") is True


def test_multilingual_transaction_action_remains_a_transaction_hint() -> None:
    assert _has_transaction_intent_hint("Envoyer 5k à Mum") is True
