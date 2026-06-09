"""Deterministic responses for planner context reads."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.accounts.mandate_state import READY, effective_mandate_status
from banking.accounts.onboarding.mandate_messages import build_pending_mandate_message
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from shared.utils.bank_aliases import BANK_ALIASES, get_bank_search_terms, normalize_bank_name

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _compact_token(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.lower())


def _find_account_for_bank_followup(text: str, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    compact_text = _compact_token(text)
    if not compact_text:
        return None

    for account in accounts:
        bank_name = str(account.get("bank_name") or "").strip()
        if not bank_name:
            continue
        search_terms = {normalize_bank_name(bank_name), *get_bank_search_terms(bank_name)}
        if any(term and _compact_token(term) in compact_text for term in search_terms):
            return account
    return None


def _extract_requested_bank_label(text: str) -> str | None:
    lowered = text.lower()
    for alias in sorted(BANK_ALIASES.keys(), key=len, reverse=True):
        if alias in lowered:
            return alias.title()
    return None


def _account_preview_lines(accounts: list[dict[str, Any]], *, limit: int) -> list[str]:
    lines: list[str] = []
    for account in accounts[:limit]:
        bank_name = str(account.get("bank_name") or account.get("bank") or "Account").strip()
        account_number = str(account.get("account_number") or account.get("number") or "").strip()
        suffix = f" • …{account_number[-4:]}" if account_number else ""
        if bank_name:
            lines.append(f"• {bank_name}{suffix}")
    return lines


def _beneficiary_preview_lines(beneficiaries: list[dict[str, Any]], *, limit: int) -> list[str]:
    lines: list[str] = []
    for beneficiary in beneficiaries[:limit]:
        alias = str(beneficiary.get("alias") or beneficiary.get("name") or "").strip()
        account_name = str(beneficiary.get("account_name") or "").strip()
        bank_name = str(beneficiary.get("bank_name") or beneficiary.get("bank") or "").strip()
        account_number = str(beneficiary.get("account_number") or beneficiary.get("account") or "").strip()
        display = alias or account_name
        if alias and account_name and alias.casefold() != account_name.casefold():
            display = f"{alias} ({account_name})"
        detail_parts = [part for part in (bank_name, f"…{account_number[-4:]}" if account_number else "") if part]
        details = f" - {' • '.join(detail_parts)}" if detail_parts else ""
        if display:
            lines.append(f"• {display}{details}")
    return lines


def _count_response(
    *,
    count: int,
    zero_key: MessageKey,
    one_key: MessageKey,
    many_key: MessageKey,
    preview_header_key: MessageKey,
    preview_lines: list[str],
    locale: str,
) -> str:
    if count == 0:
        lines = [render_message(zero_key, locale)]
    elif count == 1:
        lines = [render_message(one_key, locale)]
    else:
        lines = [render_message(many_key, locale, {"count": count})]
    if preview_lines:
        lines.extend(["", render_message(preview_header_key, locale), *preview_lines])
    return "\n".join(lines)


def synthesize_context_read_response(
    state_view: PlannerStateView,
    subtype: str,
    text: str,
    locale: str,
) -> str | None:
    """Build deterministic context-read responses from loaded context when beneficial."""
    accounts_raw = state_view.loaded_context_or_empty.get("accounts")
    accounts = accounts_raw if isinstance(accounts_raw, list) else []
    account_rows = [a for a in accounts if isinstance(a, dict)]

    if subtype == "account_count":
        return _count_response(
            count=len(account_rows),
            zero_key="account.list.count_zero",
            one_key="account.list.count_one",
            many_key="account.list.count_many",
            preview_header_key="account.list.preview_header",
            preview_lines=_account_preview_lines(account_rows, limit=3),
            locale=locale,
        )

    if subtype == "linked_accounts_summary":
        if not account_rows:
            return render_message("account.list.empty", locale)
        return "\n".join(
            [
                render_message("account.list.header", locale),
                *_account_preview_lines(account_rows, limit=5),
            ]
        )

    beneficiaries_raw = state_view.loaded_context_or_empty.get("beneficiaries")
    beneficiaries = beneficiaries_raw if isinstance(beneficiaries_raw, list) else []
    beneficiary_rows = [b for b in beneficiaries if isinstance(b, dict)]

    if subtype == "beneficiary_count":
        return _count_response(
            count=len(beneficiary_rows),
            zero_key="beneficiary.list.count_zero",
            one_key="beneficiary.list.count_one",
            many_key="beneficiary.list.count_many",
            preview_header_key="beneficiary.list.preview_header",
            preview_lines=_beneficiary_preview_lines(beneficiary_rows, limit=3),
            locale=locale,
        )

    if subtype in {"beneficiary_list", "beneficiary_name_match_preview"}:
        if not beneficiary_rows:
            return render_message("beneficiary.list.empty", locale)
        limit = 3 if subtype == "beneficiary_name_match_preview" else 5
        return "\n".join(
            [
                render_message("beneficiary.list.header", locale),
                *_beneficiary_preview_lines(beneficiary_rows, limit=limit),
            ]
        )

    if subtype != "account_linked_bank_existence_check" or not account_rows:
        return None

    match = _find_account_for_bank_followup(text, account_rows)
    if match is None:
        bank_label = _extract_requested_bank_label(text)
        if bank_label:
            return f"No, you do not have {bank_label} linked."
        return None

    bank_name = str(match.get("bank_name") or render_message("mandate.bank_fallback", locale))
    status = effective_mandate_status(match)
    if status == READY:
        return f"Yes, you have {bank_name} linked and ready."

    pending_message = build_pending_mandate_message([match], locale)
    return f"Yes, you have {bank_name} linked, but it is not ready for payments yet.\n\n{pending_message}"


def synthesize_account_context_read_response(
    state_view: PlannerStateView,
    subtype: str,
    text: str,
    locale: str,
) -> str | None:
    return synthesize_context_read_response(state_view, subtype, text, locale)


__all__ = ["synthesize_account_context_read_response", "synthesize_context_read_response"]
