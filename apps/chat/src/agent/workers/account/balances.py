"""Balance lookup for linked accounts."""

from typing import Any

from apps.chat.src.agent.workers.account.formatter import AccountFormatter
from apps.chat.src.agent.workers.account.serialization import find_account_by_bank_name, serialize_accounts
from banking.accounts.repositories.account_repository import AccountRepository
from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.banking import BankDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def check_balance(
    *,
    account_repo: AccountRepository,
    banking_provider: BankDataProvider,
    user_id: str,
    account_identifiers: list[str] | None,
    locale: str = "en",
) -> tuple[str, list[dict[str, Any]]]:
    """Check balance for specific account(s) or all accounts."""
    accounts = await account_repo.get_by_user(user_id)
    if not accounts:
        return render_message("account.no_linked_accounts", locale), []

    target_accounts = []
    if account_identifiers:
        for ident in account_identifiers:
            try:
                idx = int(ident)
                if 1 <= idx <= len(accounts):
                    target_accounts.append(accounts[idx - 1])
            except ValueError:
                found = find_account_by_bank_name(accounts, ident)
                if found:
                    target_accounts.append(found)

        if not target_accounts:
            label = ", ".join(account_identifiers)
            return render_message(
                "account.account_not_found",
                locale,
                {"identifier": label},
            ), []
    else:
        target_accounts = accounts

    balances = []
    total_balance = 0.0

    for account in target_accounts:
        try:
            bal_data = await banking_provider.get_balance(account.account_id)
            if bal_data:
                balances.append(
                    {
                        "account_id": str(getattr(account, "account_id", "")),
                        "bank_name": account.bank_name,
                        "account_number": account.account_number,
                        "amount": bal_data.available_balance,
                        "available_balance": bal_data.available_balance,
                        "currency": bal_data.currency,
                    }
                )
                total_balance += bal_data.available_balance
        except Exception as exc:
            logger.error("balance_fetch_failed", error=str(exc))

    if not balances:
        return render_message("account.balance.unavailable", locale), serialize_accounts(target_accounts)

    return (
        AccountFormatter.format_balance_response(
            balances,
            total_balance if len(balances) > 1 else None,
            locale=locale,
        ),
        balances,
    )
