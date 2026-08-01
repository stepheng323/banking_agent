from banking.transfers.extraction.parsers import parse_simple_transfer_command
from banking.transfers.models.types import TransferPayload


def test_exact_saved_alias_with_bank_word_binds_before_bank_guard() -> None:
    patch = parse_simple_transfer_command(
        "Send 10k to Tolu Access",
        TransferPayload(),
        [
            {
                "id": "bene-access",
                "alias": "Tolu Access",
                "account_name": "Tolu Adebayo",
                "bank_name": "Access Bank",
            },
            {"id": "bene-gtb", "alias": "Tolu GTB", "account_name": "Tolu Adeyemi"},
        ],
    )

    assert patch is not None
    assert patch["amount"] == 10000
    assert patch["recipient_name"] == "Tolu Access"
    assert patch["beneficiary_id"] == "bene-access"


def test_exact_alias_fastpath_refuses_ambiguous_or_extended_recipient_text() -> None:
    beneficiaries = [
        {"id": "bene-1", "alias": "Tolu Access"},
        {"id": "bene-2", "alias": "Tolu Access"},
    ]

    assert parse_simple_transfer_command("Send 10k to Tolu Access", TransferPayload(), beneficiaries) is None
    assert (
        parse_simple_transfer_command(
            "Send 10k to Tolu Access First Bank",
            TransferPayload(),
            [{"id": "bene-1", "alias": "Tolu Access"}],
        )
        is None
    )


def test_exact_alias_from_compact_context_requests_safe_hydration() -> None:
    patch = parse_simple_transfer_command(
        "Send 10k to Tolu Access",
        TransferPayload(),
        [{"id": "bene-access", "alias": "Tolu Access"}],
    )

    assert patch is not None
    assert patch["beneficiary_id"] == "bene-access"
    assert patch["beneficiary_candidates"] == [{"beneficiary_id": "bene-access", "recipient_name": "Tolu Access"}]
