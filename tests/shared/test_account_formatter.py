from banking.accounts.management.formatter import AccountFormatter
from banking.presentation.formatters.accounts import get_last4
from shared.messaging.body_blocks import render_body_blocks_text


def test_format_balance_response_single_account_mobile_blocks() -> None:
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

    assert rendered == "Your Zenith Bank account (···9384) has a balance of **₦30,000.00**."
    assert "₦30,000.00" in rendered
    assert "*Your Balance*" not in rendered
    assert "1." not in rendered


def test_format_balance_response_multi_account_mobile_blocks() -> None:
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

    assert rendered == (
        "Your Balances\n\nZenith Bank (···9384): ₦30,000.00\n\nAccess Bank (···5678): ₦15,000.00\n\nTotal: ₦45,000.00"
    )


def test_format_balance_response_blocks_render_with_mobile_spacing() -> None:
    blocks = AccountFormatter.format_balance_response_blocks(
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

    assert blocks is not None
    assert render_body_blocks_text(blocks) == ("Your Zenith Bank account (···9384) has a balance of **₦30,000.00**.")


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

    assert "First Bank" in rendered
    assert "···????" in rendered
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

    assert "First Bank" in rendered
    assert "···5262" in rendered
    assert "89f0" not in rendered
    assert "****5262" not in rendered


def test_account_list_uses_natural_status_and_action_copy() -> None:
    rendered = AccountFormatter.format_account_list(
        [
            {
                "bank_name": "Zenith Bank",
                "account_number": "1234569384",
                "mandate_status": "expired",
            },
            {
                "bank_name": "Access Bank",
                "account_number": "1234560003",
                "mandate_status": "ready",
                "is_default": True,
            },
        ],
        locale="en",
    )

    assert "1. Zenith Bank (···9384) — Unlinked" in rendered
    assert "2. Access Bank (···0003) — Default • Active" in rendered
    assert "Actions: link Zenith Bank | set 2 as default | unlink GTB" in rendered


def test_format_default_account_returns_only_masked_default_identity() -> None:
    rendered = AccountFormatter.format_default_account(
        {
            "bank_name": "GTBank",
            "account_number": "2010000002",
            "is_default": True,
        },
        locale="en",
    )

    assert rendered == "Your default account is GTBank (···0002)."
    assert "2010000002" not in rendered


def test_format_default_account_handles_missing_default() -> None:
    assert AccountFormatter.format_default_account(None, locale="en") == ("I couldn't find a default linked account.")


def test_get_last4_never_falls_back_to_internal_id_suffix() -> None:
    last4 = get_last4({"id": "b479e495-2e59-40f1-b7c3-85cb2dcd89f0"}, locale="en")

    assert last4 == "????"
