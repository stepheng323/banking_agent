from banking.support.models import SupportReferenceCandidate
from banking.support.reference_selection import (
    is_strict_receipt_selector_message,
    match_reference_candidates,
    select_recent_batch_candidates,
)


def _candidate(
    transaction_id: str,
    ordinal: int,
    amount: float,
    recipient_label: str,
) -> SupportReferenceCandidate:
    return SupportReferenceCandidate(
        transaction_id=transaction_id,
        ordinal=ordinal,
        task_type="transfer",
        amount=amount,
        recipient_name=recipient_label,
        recipient_label=recipient_label,
        final_status="success",
        receipt_allowed=True,
    )


def test_full_fresh_transfer_command_is_not_a_receipt_selector() -> None:
    candidates = [
        _candidate("tx-1", 1, 40000, "Mum"),
        _candidate("tx-2", 2, 30000, "Ay"),
    ]

    selected, selection, exhausted_message, prompt_candidates = select_recent_batch_candidates(
        message="Oh very good send 40k to mom and 30k to ay",
        candidates=candidates,
        thread_state=None,
        locale="en",
    )

    assert selected == []
    assert selection is None
    assert exhausted_message is None
    assert prompt_candidates is None


def test_amount_only_receipt_selector_can_match_candidate() -> None:
    candidates = [
        _candidate("tx-1", 1, 40000, "Mum"),
        _candidate("tx-2", 2, 30000, "Ay"),
    ]

    selected, selection, exhausted_message, prompt_candidates = select_recent_batch_candidates(
        message="40k",
        candidates=candidates,
        thread_state=None,
        locale="en",
    )

    assert [candidate.transaction_id for candidate in selected] == ["tx-1"]
    assert selection is not None
    assert exhausted_message is None
    assert prompt_candidates is None


def test_strict_receipt_selector_rejects_amount_embedded_in_fresh_command() -> None:
    assert is_strict_receipt_selector_message("40k")
    assert is_strict_receipt_selector_message("other one")
    assert is_strict_receipt_selector_message("send receipt")
    assert not is_strict_receipt_selector_message("send 40k to mom")
    assert match_reference_candidates(
        message="send 40k to mom",
        candidates=[_candidate("tx-1", 1, 40000, "Mum")],
    )
