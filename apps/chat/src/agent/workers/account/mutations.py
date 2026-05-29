"""Account mutation operations."""

import asyncio
from typing import Any

from apps.chat.src.agent.workers.account.serialization import find_account_by_bank_name
from banking.accounts.repositories.account_repository import AccountRepository
from banking.identity.repositories.user_repository import UserRepository
from banking.persistence.unit_of_work import UnitOfWork
from shared.cache.user_data import UserDataCache
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def missing_identifier_prompt(action: str, locale: str = "en") -> str:
    if action == "unlink":
        return render_message("account.prompt.unlink_identifier", locale)
    if action == "set_default":
        return render_message("account.prompt.default_identifier", locale)
    return render_message("account.prompt.which_account", locale)


async def set_default_account(
    *,
    account_repo: AccountRepository,
    user_repo: UserRepository,
    user_id: str,
    account_identifier: str,
    locale: str = "en",
) -> str:
    """Set an account as default."""
    accounts = await account_repo.get_by_user(user_id)
    if not accounts:
        return render_message("account.no_linked_accounts", locale)

    selected_account = None
    try:
        account_index = int(account_identifier)
        if 1 <= account_index <= len(accounts):
            selected_account = accounts[account_index - 1]
    except ValueError:
        selected_account = find_account_by_bank_name(accounts, account_identifier)

    if not selected_account:
        return render_message(
            "account.account_not_found_with_count",
            locale,
            {
                "identifier": account_identifier,
                "count": len(accounts),
            },
        )

    try:
        async with UnitOfWork() as uow:
            await uow.accounts.set_default_account(user_id, str(selected_account.account_id))
            await uow.commit()

        user = await user_repo.get_by_id(user_id)
        if user:
            asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))

        masked = f"***{selected_account.account_number[-4:]}"
        return render_message(
            "account.default_updated",
            locale,
            {
                "bank_name": selected_account.bank_name,
                "masked": masked,
            },
        )
    except Exception as exc:
        logger.error(f"set_default_error: {exc}")
        return render_message("account.error.default_update_failed", locale)


async def unlink_account(
    *,
    account_repo: Any,
    direct_debit_provider: Any,
    user_id: str,
    account_identifier: str,
    locale: str = "en",
) -> str:
    """Unlink an account."""
    accounts = await account_repo.get_by_user(user_id)
    if not accounts:
        return render_message("account.no_linked_accounts", locale)
    if len(accounts) == 1:
        return render_message("account.unlink.only_account", locale)

    selected_account = None
    try:
        account_index = int(account_identifier)
        if 1 <= account_index <= len(accounts):
            selected_account = accounts[account_index - 1]
    except ValueError:
        selected_account = find_account_by_bank_name(accounts, account_identifier)

    if not selected_account:
        return render_message(
            "account.account_not_found",
            locale,
            {"identifier": account_identifier},
        )

    try:
        if getattr(selected_account, "mandate_id", None):
            try:
                await direct_debit_provider.cancel_mandate(selected_account.mandate_id)
            except Exception:
                pass

        async with UnitOfWork() as uow:
            success = await uow.accounts.delete_account(str(selected_account.account_id), user_id)
            await uow.commit()
        if success:
            try:
                async with UnitOfWork() as uow:
                    if uow.users:
                        user = await uow.users.get_by_id(user_id)
                        if user:
                            asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))
            except Exception:
                pass

            masked = f"***{selected_account.account_number[-4:]}"
            return render_message(
                "account.unlink.success",
                locale,
                {
                    "bank_name": selected_account.bank_name,
                    "masked": masked,
                },
            )
        return render_message("account.error.unlink_failed", locale)
    except Exception as exc:
        logger.error(f"unlink_error: {exc}")
        return render_message("account.error.unlink_failed", locale)
