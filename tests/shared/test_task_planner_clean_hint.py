from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import (
    _augment_context_with_clean_transfer_hint,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import (
    PlannerPromptSignals,
)


def test_clean_transfer_hint_preserves_alias_allocations() -> None:
    context = _augment_context_with_clean_transfer_hint(
        "None",
        "Send 2k each to Tolu Access and Tolu GTB",
        PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )

    assert "exactly one transfer send_money task" in context
    assert "amount=2000" in context
    assert 'recipient_name:"Tolu Access",amount:2000' in context
    assert 'recipient_name:"Tolu GTB",amount:2000' in context
    assert "Omit recipient_name and bank_name at task parameters level" in context
    assert "do not turn alias words into bank_name" in context


def test_clean_transfer_hint_omitted_for_single_recipient_alias() -> None:
    context = _augment_context_with_clean_transfer_hint(
        "None",
        "Send 2k to Tolu GTB",
        PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )

    assert context == "None"


def test_clean_transfer_hint_splits_total_amount_between_recipients() -> None:
    context = _augment_context_with_clean_transfer_hint(
        "Recent state",
        "Split 20k between Adebayo and Mum",
        PlannerPromptSignals(expected_transaction_executors=("transfer",)),
    )

    assert "Recent state" in context
    assert "amount=10000" in context
    assert 'recipient_name:"Adebayo",amount:10000' in context
    assert 'recipient_name:"Mum",amount:10000' in context


def test_clean_transfer_hint_adds_source_aware_guidance() -> None:
    context = _augment_context_with_clean_transfer_hint(
        "None",
        "Use GTBank to send 5k to Tolu Access for lunch",
        PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )

    assert "CLEAN_SOURCE_TRANSFER_HINT_JSON" in context
    assert '"source_bank_name":"GTBank"' in context
    assert '"recipient_name":"Tolu Access"' in context
    assert '"bank_name":null' not in context
    assert '"narration":"Lunch"' in context
    assert "omit recipient_bank_name and bank_name" in context
    assert 'Treat every word in "Tolu Access" as recipient alias text' in context
    assert 'Wrong: {"bank_name":"Access Bank"}' in context


def test_clean_transfer_hint_omits_source_hint_without_source_first_phrase() -> None:
    context = _augment_context_with_clean_transfer_hint(
        "None",
        "Send 5k to Tolu Access for lunch",
        PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )

    assert context == "None"
