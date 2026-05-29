"""Helpers for explicit source-account selection against ready vs linked accounts."""

from __future__ import annotations

from typing import Any

from shared.i18n.renderer import render_message
from banking.accounts.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.bank_aliases import get_bank_search_terms, normalize_bank_name


def find_account_by_id(accounts: list[dict[str, Any]], account_id: str | None) -> dict[str, Any] | None:
    if not account_id:
        return None
    for account in accounts:
        if str(account.get("id")) == str(account_id):
            return account
    return None


def find_account_by_index(accounts: list[dict[str, Any]], account_index: int | None) -> dict[str, Any] | None:
    if account_index is None:
        return None
    index = account_index - 1
    if 0 <= index < len(accounts):
        return accounts[index]
    return None


def find_account_by_bank_name(accounts: list[dict[str, Any]], bank_name: str | None) -> dict[str, Any] | None:
    if not bank_name:
        return None

    search_terms = {normalize_bank_name(bank_name), *get_bank_search_terms(bank_name)}
    for account in accounts:
        label = str(account.get("bank_name") or "").strip()
        if not label:
            continue
        normalized_label = normalize_bank_name(label)
        compact_label = normalized_label.replace(" ", "")
        if any(
            term
            and (
                term in normalized_label
                or normalized_label in term
                or term.replace(" ", "") in compact_label
                or compact_label in term.replace(" ", "")
            )
            for term in search_terms
        ):
            return account
    return None


def is_account_ready(account: dict[str, Any] | None) -> bool:
    if not account:
        return False
    status = str(account.get("mandate_status") or "").strip().lower()
    return status == "ready"


def build_nonready_source_account_message(account: dict[str, Any], locale: str) -> str:
    bank_name = str(account.get("bank_name") or render_message("mandate.bank_fallback", locale))
    status = str(account.get("mandate_status") or "").strip().lower()
    if status == "pending":
        pending_message = build_pending_mandate_message([account], locale)
        return f"Your {bank_name} account is linked, but it is not ready for payments yet.\n\n{pending_message}"
    return (
        f"Your {bank_name} account is linked, but it is not ready for direct debit yet.\n\n"
        f"{render_message('mandate.status_not_ready', locale)}"
    )
