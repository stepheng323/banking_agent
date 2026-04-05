from shared.formatters.accounts import format_source_account_info_from_account_number
from shared.formatters.airtime import format_airtime_summary
from shared.formatters.transfer import format_multi_source_transfer_summary, format_transfer_summary


def test_source_account_info_from_number_with_balance() -> None:
    line = format_source_account_info_from_account_number(
        bank="First Bank",
        account_number="1234567890",
        locale="en",
        balance=30000,
    )
    assert line == "From: First Bank (···7890) • Bal: ₦30,000.00"


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
