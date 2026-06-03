from __future__ import annotations

from datetime import timedelta

from banking.accounts.management.formatter import AccountFormatter
from banking.accounts.mandate_state import (
    APPROVED,
    EXPIRED,
    READY,
    effective_mandate_status,
    is_mandate_authorization_expired,
    is_mandate_debit_ready,
    is_mandate_transition_allowed,
    mandate_authorization_metadata,
)
from shared.utils.datetime import utc_now_naive


def test_pending_mandate_becomes_locally_expired_after_authorization_window() -> None:
    created_at = utc_now_naive() - timedelta(hours=2)
    account = {
        "mandate_status": "pending",
        "extra_data": mandate_authorization_metadata(
            {},
            created_at=created_at,
            transfer_destinations=[{"bank_name": "NIBSS Bank", "account_number": "0001112223"}],
        ),
    }

    assert is_mandate_authorization_expired(account)
    assert effective_mandate_status(account) == EXPIRED
    assert not is_mandate_debit_ready(account)


def test_unexpired_pending_mandate_is_not_ready() -> None:
    created_at = utc_now_naive()
    account = {
        "mandate_status": "pending",
        "extra_data": mandate_authorization_metadata({}, created_at=created_at, transfer_destinations=[]),
    }

    assert effective_mandate_status(account) == "pending"
    assert not is_mandate_debit_ready(account)


def test_only_ready_mandate_is_debit_ready() -> None:
    assert is_mandate_debit_ready({"mandate_status": READY})
    assert not is_mandate_debit_ready({"mandate_status": APPROVED})
    assert not is_mandate_debit_ready({"mandate_status": "active"})


def test_mandate_transition_guards_block_stale_updates() -> None:
    assert not is_mandate_transition_allowed(READY, APPROVED, event_name="events.mandates.approved")
    assert not is_mandate_transition_allowed(EXPIRED, READY, event_name="events.mandates.ready")
    assert is_mandate_transition_allowed("paused", READY, event_name="events.mandate.action.reinstate")
    assert not is_mandate_transition_allowed("paused", READY, event_name="events.mandates.ready")
    assert is_mandate_transition_allowed("active", READY, event_name="events.mandates.ready")
    assert is_mandate_transition_allowed("active", EXPIRED, event_name="events.mandates.expired")


def test_account_discovery_lists_non_ready_accounts_with_status_icon() -> None:
    created_at = utc_now_naive() - timedelta(hours=2)
    accounts = [
        {
            "bank_name": "First Bank",
            "account_number": "1234567890",
            "mandate_status": "pending",
            "extra_data": mandate_authorization_metadata({}, created_at=created_at, transfer_destinations=[]),
        },
        {"bank_name": "GTBank", "account_number": "0000001234", "mandate_status": READY},
    ]

    response = AccountFormatter.format_account_list(accounts)

    assert "First Bank" in response
    assert "GTBank" in response
    assert "[!]" in response
    assert "[✓]" in response
