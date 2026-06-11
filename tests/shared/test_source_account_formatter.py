from banking.presentation.formatters.accounts import format_source_account_info_from_account_number
from banking.presentation.formatters.airtime import format_airtime_summary
from banking.presentation.formatters.confirmation import build_source_account_info
from banking.presentation.formatters.data import format_data_summary
from banking.presentation.formatters.transfer_multi_source import format_multi_source_transfer_summary
from banking.presentation.formatters.transfer_notifications import format_transfer_success_message
from banking.presentation.formatters.transfer_summary import (
    format_transfer_summary,
)


def test_source_account_info_from_number_with_balance() -> None:
    line = format_source_account_info_from_account_number(
        bank="First Bank",
        account_number="1234567890",
        locale="en",
        balance=30000,
    )
    assert line == "From: First Bank (···7890) • Bal: ₦30,000.00"


def test_source_account_info_resolves_number_from_source_account_id() -> None:
    line = build_source_account_info(
        task_payload={"source_account_id": "acct-access", "source_bank_name": "Access Bank"},
        snapshot={},
        accounts=[
            {
                "id": "acct-access",
                "bank_name": "Access Bank",
                "account_number": "1234500003",
            }
        ],
        locale="en",
    )

    assert line == "From: Access Bank (···0003)"


def test_pidgin_source_account_info_uses_clear_banking_label() -> None:
    line = build_source_account_info(
        task_payload={"source_account_id": "acct-access", "source_bank_name": "Access Bank"},
        snapshot={},
        accounts=[
            {
                "id": "acct-access",
                "bank_name": "Access Bank",
                "account_number": "1234500003",
            }
        ],
        locale="pcm",
    )

    assert line == "From account: Access Bank (···0003)"
    assert "Comot from" not in line


def test_source_account_info_uses_serialized_account_last4_when_number_is_encrypted() -> None:
    line = build_source_account_info(
        task_payload={"source_account_id": "acct-access", "source_bank_name": "Access Bank"},
        snapshot={},
        accounts=[
            {
                "id": "acct-access",
                "bank_name": "Access Bank",
                "account_number_last4": "5262",
            }
        ],
        locale="en",
    )

    assert line == "From: Access Bank (···5262)"


def test_transfer_summary_uses_shared_source_line_formatter() -> None:
    summary = format_transfer_summary(
        {
            "amount": 5000,
            "recipientName": "Fatima Zahra Musa",
            "recipientBank": "Access Bank",
            "recipientAccount": "8067892221",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
        },
        include_source=True,
        locale="en",
    )
    assert "From: First Bank (···7890)" in summary


def test_transfer_summary_preserves_canonical_bank_display() -> None:
    summary = format_transfer_summary(
        {
            "amount": 5000,
            "recipientName": "Tolu Adebayo",
            "recipientBank": "GTB",
            "recipientAccount": "8067892221",
            "sourceBank": "access",
            "sourceAccount": "1234567890",
        },
        include_source=True,
        locale="en",
    )

    assert "GTBank • 8067892221" in summary
    assert "Gtbank • 8067892221" not in summary
    assert "From: Access Bank (···7890)" in summary


def test_transfer_success_message_omits_provider_transaction_id() -> None:
    message = format_transfer_success_message(
        amount=5000,
        recipient_name="Tolu Adebayo",
        transaction_id="mock_debit_5e23f4cd782d",
        locale="en",
    )

    assert message == "✓ Transfer successful! ₦5,000 has been sent to Tolu Adebayo."
    assert "Transaction ID" not in message
    assert "mock_debit" not in message


def test_airtime_summary_uses_shared_source_line_formatter() -> None:
    summary = format_airtime_summary(
        {
            "amount": 1000,
            "recipientPhone": "08012345678",
            "network": "MTN",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
        },
        locale="en",
    )
    assert "From: First Bank (···7890)" in summary


def test_airtime_summary_displays_network_label_naturally() -> None:
    summary = format_airtime_summary(
        {
            "amount": 1000,
            "recipientPhone": "08012345678",
            "network": "AIRTEL",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
        },
        locale="en",
    )

    assert "Network: Airtel" in summary
    assert "Network: AIRTEL" not in summary


def test_data_summary_uses_shared_source_line_formatter() -> None:
    summary = format_data_summary(
        {
            "planName": "MTN 2GB",
            "amount": 1500,
            "recipientPhone": "08012345678",
            "network": "MTN",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
        },
        locale="en",
    )
    assert "From: First Bank (···7890)" in summary


def test_data_summary_displays_network_label_naturally() -> None:
    summary = format_data_summary(
        {
            "planName": "Airtel 2GB",
            "amount": 1500,
            "recipientPhone": "08012345678",
            "network": "AIRTEL",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
        },
        locale="en",
    )

    assert "Network: Airtel" in summary
    assert "Network: AIRTEL" not in summary


def test_airtime_summary_uses_recipient_name_for_display() -> None:
    summary = format_airtime_summary(
        {
            "amount": 500,
            "recipientPhone": "08162511023",
            "recipientName": "Tolu",
            "network": "MTN",
            "sourceBank": "Access Bank",
            "sourceAccount": "1234500003",
        },
        locale="en",
    )

    assert "airtime for Tolu (08162511023)" in summary


def test_transfer_summary_narration_is_plain_text_without_markdown_italics() -> None:
    summary = format_transfer_summary(
        {
            "amount": 5000,
            "recipientName": "Fatima Zahra Musa",
            "recipientBank": "Access Bank",
            "recipientAccount": "8067892221",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
            "user_note": "feeding",
        },
        include_source=True,
        locale="en",
    )
    assert "Narration: Feeding" in summary
    assert "Narration: _Feeding_" not in summary


def test_transfer_summary_prefers_authored_narration_over_legacy_user_note() -> None:
    summary = format_transfer_summary(
        {
            "amount": 5000,
            "recipientName": "Fatima Zahra Musa",
            "recipientBank": "Access Bank",
            "recipientAccount": "8067892221",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
            "authored_narration": "school fees",
            "user_note": "feeding",
            "narration": "salary",
        },
        include_source=True,
        locale="en",
    )
    assert "Narration: School fees" in summary
    assert "Narration: Feeding" not in summary


def test_transfer_summary_hides_execution_default_narration_without_user_note() -> None:
    summary = format_transfer_summary(
        {
            "amount": 5000,
            "recipientName": "Fatima Zahra Musa",
            "recipientBank": "Access Bank",
            "recipientAccount": "8067892221",
            "sourceBank": "First Bank",
            "sourceAccount": "1234567890",
            "narration": "Transfer to Fatima Zahra Musa",
        },
        include_source=True,
        locale="en",
    )
    assert "Narration:" not in summary


def test_multi_source_summary_narration_is_plain_text_without_markdown_italics() -> None:
    summary = format_multi_source_transfer_summary(
        {
            "amount": 50000,
            "recipientName": "John Doe",
            "recipientBank": "GTBank",
            "recipientAccount": "1234567890",
            "funding_sources": [
                {"bank_name": "UBA", "account_number": "1111111111", "amount": 30000},
                {"bank_name": "Access", "account_number": "2222222222", "amount": 20000},
            ],
            "user_note": "feeding",
        },
        locale="en",
    )
    assert "Narration: Feeding" in summary
    assert "Narration: _Feeding_" not in summary


def test_multi_source_summary_hides_execution_default_narration_without_user_note() -> None:
    summary = format_multi_source_transfer_summary(
        {
            "amount": 50000,
            "recipientName": "John Doe",
            "recipientBank": "GTBank",
            "recipientAccount": "1234567890",
            "funding_sources": [
                {"bank_name": "UBA", "account_number": "1111111111", "amount": 30000},
                {"bank_name": "Access", "account_number": "2222222222", "amount": 20000},
            ],
            "narration": "Transfer to John Doe",
        },
        locale="en",
    )
    assert "Narration:" not in summary
