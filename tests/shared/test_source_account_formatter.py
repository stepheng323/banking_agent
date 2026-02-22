from shared.formatters.accounts import format_source_account_info_from_account_number
from shared.formatters.airtime import format_airtime_summary
from shared.formatters.transfer import format_transfer_summary


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
