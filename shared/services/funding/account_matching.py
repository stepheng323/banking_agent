"""Account matching and message helpers for funding plans."""

from __future__ import annotations

from typing import Any

import shared.services.funding.models as funding_models
from shared.i18n.renderer import render_message
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.bank_aliases import normalize_bank_name


def match_account_by_bank_name(accounts: list[Any], bank_name: str) -> Any | None:
    target = compact_bank_name(bank_name)
    if not target:
        return None
    for account in accounts:
        candidate_name = compact_bank_name(getattr(account, "bank_name", "") or "")
        if target == candidate_name or target in candidate_name or candidate_name in target:
            return account
    return None


def compact_bank_name(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum())


def create_step(account: Any, amount: float, sequence: int) -> funding_models.FundingStepPlan:
    return funding_models.FundingStepPlan(
        account_id=account.id,
        account_number=account.account_number,
        bank_name=account.bank_name,
        mandate_id=account.mandate_id,
        amount=amount,
        sequence=sequence,
    )


def is_eligible(account: Any) -> bool:
    return account.mandate_status == "ready" and account.mandate_id is not None


def match_ineligible_requested_account(all_accounts: list[Any], eligible: list[Any], bank_name: str) -> Any | None:
    eligible_ids = {str(getattr(account, "id", "")) for account in eligible}
    normalized_request = normalize_bank_name(bank_name)
    for account in all_accounts:
        if str(getattr(account, "id", "")) in eligible_ids:
            continue
        account_bank = str(getattr(account, "bank_name", "") or "")
        normalized_bank = normalize_bank_name(account_bank)
        if (
            normalized_request
            and (
                normalized_request in normalized_bank
                or normalized_bank in normalized_request
                or normalized_request.replace(" ", "") in normalized_bank.replace(" ", "")
            )
        ):
            return account
    return None


def build_explicit_nonready_account_message(account: Any, locale: str) -> str:
    account_dict = {
        "bank_name": getattr(account, "bank_name", ""),
        "account_number": getattr(account, "account_number", ""),
        "mandate_status": getattr(account, "mandate_status", ""),
        "extra_data": getattr(account, "extra_data", {}) or {},
    }
    bank_name = str(account_dict.get("bank_name") or render_message("mandate.bank_fallback", locale))
    pending_message = build_pending_mandate_message([account_dict], locale)
    return f"Your {bank_name} account is linked, but it is not ready for payments yet.\n\n{pending_message}"


def build_pending_mandate_message_for_account(account: Any, locale: str) -> str:
    """Build contextual message for accounts with pending mandates."""
    from shared.services.onboarding.mandate import MandateService

    extra_data: dict = getattr(account, "extra_data", None) or {}
    destinations: list[dict] = extra_data.get("transfer_destinations", [])
    account_number: str = getattr(account, "account_number", "") or ""
    bank_name: str = getattr(account, "bank_name", "") or ""

    if destinations:
        svc = MandateService()
        return svc.build_mandate_auth_message(
            account_number=account_number,
            bank_name=bank_name,
            transfer_destinations=destinations,
        )

    return render_message("mandate.pending_complete_transfer", locale)
