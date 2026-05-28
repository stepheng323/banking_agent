from apps.chat.src.agent.workers.account.formatter import AccountFormatter


def test_format_balance_response_single_account_natural_sentence() -> None:
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

    assert "Your Zenith Bank account" in rendered
    assert "(···9384)" in rendered
    assert "₦30,000.00" in rendered
    assert "*Your Balance*" not in rendered
    assert "1." not in rendered


def test_format_balance_response_multi_account_bullet_points() -> None:
    rendered = AccountFormatter.format_balance_response(
        [
            {
                "bank_name": "Zenith Bank",
                "account_number": "0000009384",
                "amount": 30000.0,
                "currency": "NGN",
            },
            {
                "bank_name": "Access Bank",
                "account_number": "1234565678",
                "amount": 15000.0,
                "currency": "NGN",
            },
        ],
        total_balance=45000.0,
        locale="en",
    )

    assert "•" in rendered
    assert "Zenith Bank" in rendered
    assert "Access Bank" in rendered
    assert "(···9384)" in rendered
    assert "(···5678)" in rendered
    assert "Here are your account balances" in rendered
    assert "total of" in rendered
    assert "₦45,000.00" in rendered
