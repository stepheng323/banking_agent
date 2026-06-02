"""Affordability query handler."""

from decimal import Decimal
from typing import Any

from apps.chat.src.agent.workers.query.models.domain import (
    QueryExecutionContract,
    QueryResult,
)
from banking.accounts.mandate_state import is_mandate_debit_ready
from banking.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS
from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.banking import BankDataProvider
from shared.money import MoneyAmount, to_naira


async def handle_affordability(
    provider: BankDataProvider,
    contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    **kwargs: Any,
) -> QueryResult:
    """Handle affordability queries."""
    del current_page, page_size, user_id
    language = kwargs.get("language", "en")
    amount = to_naira(contract.amount_check) or Decimal("0.00")
    candidate_ids = account_ids or [account_id]
    account_lookup = _account_lookup(accounts_info or [])

    if contract.accounts_scope == "all" and len(candidate_ids) > 1:
        return await _handle_multi_account_affordability(
            provider=provider,
            account_ids=candidate_ids,
            account_lookup=account_lookup,
            amount=amount,
            language=language,
        )

    # Get balance
    balance = await provider.get_balance(account_id, real_time=True)
    if not balance:
        return QueryResult(summary_text=render_message("query.affordability.balance_unavailable", language))

    can_afford = balance.available_balance >= amount
    remaining = balance.available_balance - amount

    if can_afford:
        msg = render_message(
            "query.affordability.can_afford",
            language,
            {
                "balance": f"{balance.available_balance:,.2f}",
                "amount": f"{amount:,.0f}",
                "remaining": f"{remaining:,.0f}",
            },
        )
        return QueryResult(summary_text=msg)

    shortfall = amount - balance.available_balance
    msg = render_message(
        "query.affordability.cannot_afford",
        language,
        {
            "amount": f"{amount:,.0f}",
            "balance": f"{balance.available_balance:,.2f}",
            "shortfall": f"{shortfall:,.0f}",
        },
    )
    return QueryResult(summary_text=msg)


async def _handle_multi_account_affordability(
    *,
    provider: BankDataProvider,
    account_ids: list[str],
    account_lookup: dict[str, dict],
    amount: MoneyAmount,
    language: str,
) -> QueryResult:
    balances: list[tuple[str, str, str, MoneyAmount]] = []
    for candidate_id in account_ids:
        account = account_lookup.get(candidate_id, {})
        if account and not is_mandate_debit_ready(account):
            continue
        balance = await provider.get_balance(candidate_id, real_time=True)
        if balance is None:
            continue
        balances.append(
            (
                candidate_id,
                _account_bank_name(account, candidate_id),
                _account_suffix(account),
                balance.available_balance,
            )
        )

    if not balances:
        return QueryResult(summary_text=render_message("query.affordability.balance_unavailable", language))

    single = next((entry for entry in balances if entry[3] >= amount), None)
    if single is not None:
        _candidate_id, bank_name, suffix, available = single
        remaining = available - amount
        return QueryResult(
            summary_text=(
                f"You can cover {_format_naira(amount)} from {bank_name}{suffix}.\n"
                f"Available balance: {_format_naira(available)}\n"
                f"Balance after: {_format_naira(remaining)}"
            )
        )

    pooled = _build_pool_plan(balances, amount)
    if sum(entry[3] for entry in pooled) >= amount:
        lines = [
            f"No single account covers {_format_naira(amount)}, but your ready accounts can cover it with pooling.",
            "",
            "Suggested breakdown:",
        ]
        remaining = amount
        for _candidate_id, bank_name, suffix, available in pooled:
            contribution = min(available, remaining)
            remaining -= contribution
            lines.append(f"• {bank_name}{suffix}: {_format_naira(contribution)}")
        lines.append(f"Total: {_format_naira(amount)}")
        return QueryResult(summary_text="\n".join(lines))

    total_available = sum((entry[3] for entry in balances), Decimal("0.00"))
    shortfall = max(Decimal("0.00"), amount - total_available)
    return QueryResult(
        summary_text=(
            f"Your ready accounts cannot cover {_format_naira(amount)} right now.\n"
            f"Available across ready accounts: {_format_naira(total_available)}\n"
            f"Shortfall: {_format_naira(shortfall)}"
        )
    )


def _account_lookup(accounts_info: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for account in accounts_info:
        for key in ("account_id", "mono_account_id", "id"):
            value = account.get(key)
            if value:
                lookup[str(value)] = account
    return lookup


def _account_bank_name(account: dict, fallback: str) -> str:
    return str(account.get("bank_name") or account.get("institution_name") or fallback)


def _account_suffix(account: dict) -> str:
    account_number = str(account.get("account_number") or "").strip()
    if len(account_number) >= 4:
        return f" (···{account_number[-4:]})"
    return ""


def _build_pool_plan(balances: list[tuple[str, str, str, MoneyAmount]], amount: MoneyAmount) -> list[tuple[str, str, str, MoneyAmount]]:
    ordered = sorted(balances, key=lambda entry: entry[3], reverse=True)
    plan: list[tuple[str, str, str, MoneyAmount]] = []
    total = Decimal("0.00")
    for entry in ordered[:MAX_POOLED_SOURCE_ACCOUNTS]:
        if entry[3] <= 0:
            continue
        plan.append(entry)
        total += entry[3]
        if total >= amount:
            break
    return plan


def _format_naira(value: MoneyAmount) -> str:
    return format_naira_compact(value)
