from banking.accounts.management.formatter import AccountFormatter
from banking.presentation.formatters.accounts import get_last4


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


def test_account_list_does_not_mask_internal_account_id_as_account_number() -> None:
    rendered = AccountFormatter.format_account_list(
        [
            {
                "id": "b479e495-2e59-40f1-b7c3-85cb2dcd89f0",
                "bank_name": "First Bank",
                "mandate_status": "ready",
            }
        ],
        locale="en",
    )

    assert "First Bank (···????)" in rendered
    assert "89f0" not in rendered


def test_account_list_uses_explicit_account_number_last4_when_number_is_not_available() -> None:
    rendered = AccountFormatter.format_account_list(
        [
            {
                "id": "b479e495-2e59-40f1-b7c3-85cb2dcd89f0",
                "bank_name": "First Bank",
                "account_number_last4": "5262",
                "mandate_status": "ready",
            }
        ],
        locale="en",
    )

    assert "First Bank (···5262)" in rendered
    assert "89f0" not in rendered
    assert "****5262" not in rendered


def test_get_last4_never_falls_back_to_internal_id_suffix() -> None:
    last4 = get_last4({"id": "b479e495-2e59-40f1-b7c3-85cb2dcd89f0"}, locale="en")

    assert last4 == "????"
