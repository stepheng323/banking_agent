from apps.core.src.agent.graphs.account.formatter import AccountFormatter


def test_format_balance_response_uses_markdown_safe_account_mask() -> None:
    rendered = AccountFormatter.format_balance_response(
        [
            {
                "bank_name": "Zenith Bank",
                "account_number": "0000009384",
                "amount": 30000.0,
                "currency": "NGN",
            }
        ],
        total_balance=None,
        locale="en",
    )

    assert "(···9384)" in rendered
    assert "(****9384)" not in rendered
