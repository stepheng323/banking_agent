"""Service for managing user bank accounts."""

import asyncio
import time
from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.account.formatter import AccountFormatter
from apps.core.src.agent.graphs.account.parser import (
    AccountParser,
)
from apps.core.src.agent.graphs.account.worker import AccountWorker
from shared.cache.flow_session_manager import FlowSessionManager
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankingDataProvider
from shared.clients.abstractions.messaging import MessagingClient
from shared.config import settings
from shared.models.account import Account
from shared.repositories.account_repository import AccountRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.repositories.user_repository import UserRepository
from shared.services.onboarding.session import OnboardingStep
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountService:
    """Service for managing user bank accounts (link, unlink, list, set default)."""

    def __init__(
        self,
        account_repo: AccountRepository,
        user_repo: UserRepository,
        llm: ChatOpenAI,
        messaging_client: MessagingClient,
        banking_provider: BankingDataProvider,
        session_manager: FlowSessionManager,
    ):
        """
        Initialize account management service.

        Args:
            account_repo: Repository for account operations
            user_repo: Repository for user operations
            llm: Language model for intent parsing
            whatsapp_client: WhatsApp client for sending flows
        """
        self.account_repo = account_repo
        self.user_repo = user_repo
        self.llm = llm
        self.messaging_client = messaging_client
        self.banking_provider = banking_provider
        self.session_manager = session_manager
        self.parser = AccountParser(llm)
        self.worker = AccountWorker(self)

    async def _translate_response(self, text: str, language: str) -> str:
        """Translate response to user's preferred language using LLM."""
        try:
            prompt = (
                f"Translate the following banking assistant response to {language}. "
                "Keep the formatting (markdown, emojis) exactly the same. "
                "Adapt the tone to be natural in the target language\n\n"
                "(e.g., Use Pidgin English style if language is Pidgin).\n\n"
                f"Original Response:\n{text}"
            )
            result = await self.llm.ainvoke(prompt)
            if hasattr(result, "content"):
                return result.content
            return str(result)
        except Exception:
            return text

    async def build_link_account_flow(self, context: dict[str, Any]) -> dict[str, Any]:
        """Build flow config for account linking without sending it."""
        flow_id = settings.account_linking_flow_id
        if not flow_id:
            return {"error": "Sorry, account linking is temporarily unavailable. Please contact support."}

        phone_number = context.get("phone_number", "")
        timestamp = int(time.time())
        flow_token = f"link-{phone_number}-{timestamp}"

        profile = context.get("profile") or {}
        extra_data = profile.get("extra_data") or {}
        bvn = extra_data.get("bvn")

        if not bvn:
            return {"error": "BVN not found. Please complete onboarding first."}

        result = await self.banking_provider.initiate_bvn_lookup(bvn)
        methods = [{"id": m["method"], "title": m["hint"]} for m in result.verification_methods]

        if not result.success:
            error_msg = result.error_message or "Failed to start account linking."
            logger.error("account_linking_init_failed", phone=phone_number, error=error_msg)
            return {"error": error_msg}

        await self.session_manager.update_session(
            flow_token,
            {
                "phone_number": phone_number,
                "bvn": bvn,
                "session_id": result.session_id,
                "methods": methods,
                "step": OnboardingStep.METHOD_SELECTION.value,
                "is_account_linking": True,
            },
        )

        flow_config = {
            "header": "Link New Account",
            "text_body": "Tap Continue to link a new bank account.",
            "flow_cta": "Link Account",
            "screen_name": "METHOD_SELECTION",
            "flow_token": flow_token,
            "flow_action_payload": {
                "screen": "METHOD_SELECTION",
                "data": {
                    "methods": methods,
                    "bvn": result.bvn,
                },
            },
        }

        fallback_text = (
            f"To link your account, please visit: https://fusepay.io/link/{flow_token}\n\n"
            "(This channel doesn't support interactive forms yet)"
        )

        return {
            "flow_id": flow_id,
            "flow_config": flow_config,
            "fallback_text": fallback_text,
        }

    async def list_accounts(self, user_id: str) -> str:
        """
        List all linked accounts for a user.

        Args:
            user_id: User ID

        Returns:
            Formatted message with an account list
        """
        accounts = await self.account_repo.get_by_user(user_id)
        return AccountFormatter.format_account_list(accounts)

    async def set_default(self, user_id: str, account_identifier: str) -> str:
        """
        Set an account as default by its index (1-based) or bank name.

        Args:
            user_id: User ID
            account_identifier: Account index (1, 2, etc.) or bank name (GTB, UBA, etc.)

        Returns:
            Success or error message
        """
        accounts = await self.account_repo.get_by_user(user_id)

        if not accounts:
            return "You don't have any linked accounts."

        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)

        if not selected_account:
            return (
                f"I couldn't find an account matching '{account_identifier}'.\n\n"
                f"You have {len(accounts)} linked account(s). "
                f"Please use a number (1-{len(accounts)}) or a bank name like 'GTB', 'UBA', 'Access', etc."
            )

        try:
            async with UnitOfWork() as uow:
                await uow.accounts.set_default_account(user_id, str(selected_account.account_id))
                await uow.commit()

            user = await self.user_repo.get_by_id(user_id)
            if user:
                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))

            masked_number = f"***{selected_account.account_number[-4:]}"
            return (
                f"✓ *Default account updated!*\n\n"
                f"{selected_account.bank_name} ({masked_number}) is now your default account.\n\n"
                f"All transactions will use this account unless you specify otherwise."
            )
        except Exception as e:
            logger.error(
                "set_default_account_error",
                user_id=user_id,
                account_id=str(selected_account.account_id),
                error=str(e),
                exc_info=True,
            )
            return "Sorry, I couldn't update your default account. Please try again."

    async def unlink_account(self, user_id: str, account_identifier: str) -> str:
        """
        Unlink (delete) an account by its index (1-based) or bank name.

        Args:
            user_id: User ID
            account_identifier: Account index (1, 2, etc.) or bank name (GTB, UBA, etc.)

        Returns:
            Success or error message
        """
        accounts = await self.account_repo.get_by_user(user_id)

        if not accounts:
            return "You don't have any linked accounts."

        if len(accounts) == 1:
            return (
                "⚠️ You can't unlink your only account.\n\n"
                "You need at least one account to use the banking agent. "
                "If you want to switch accounts, link a new one first, then unlink this one."
            )

        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)

        if not selected_account:
            return (
                f"I couldn't find an account matching '{account_identifier}'.\n\n"
                f"You have {len(accounts)} linked account(s). "
                f"Please use a number (1-{len(accounts)}) or a bank name like 'GTB', 'UBA', 'Access', etc."
            )

        try:
            mandate_id = getattr(selected_account, "mandate_id", None)
            if mandate_id and self.direct_debit_provider:
                try:
                    await self.direct_debit_provider.cancel_mandate(mandate_id)
                    logger.info("mandate_cancelled_for_unlink", mandate_id=mandate_id)
                except Exception as e:
                    logger.warning("cancel_mandate_failed_on_unlink", mandate_id=mandate_id, error=str(e))

            success = await self.account_repo.delete_account(str(selected_account.account_id), user_id)

            if success:
                try:
                    async with UnitOfWork() as uow:
                        if uow.users:
                            user = await uow.users.get_by_id(user_id)
                            if user:
                                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))
                except Exception:
                    pass

                masked_number = f"***{selected_account.account_number[-4:]}"
                return (
                    f"✓ *Account unlinked!*\n\n"
                    f"{selected_account.bank_name} ({masked_number}) has been removed.\n\n"
                    f"You now have {len(accounts) - 1} linked account(s)."
                )
            else:
                return "Sorry, I couldn't unlink that account. Please try again."
        except Exception as e:
            logger.error(
                "unlink_account_error",
                user_id=user_id,
                account_id=str(selected_account.account_id),
                error=str(e),
                exc_info=True,
            )
            return "Sorry, I couldn't unlink that account. Please try again."

    def _find_account_by_bank_name(self, accounts: list[Account], bank_name: str) -> Account | None:
        """
        Find an account by bank name (fuzzy matching).

        Args:
            accounts: List of accounts to search
            bank_name: Bank name or abbreviation (case-insensitive)

        Returns:
            Matching account or None
        """
        from shared.utils.bank_aliases import normalize_bank_name

        bank_name_lower = bank_name.lower().strip()
        normalized_search = normalize_bank_name(bank_name)

        for account in accounts:
            account_bank_lower = account.bank_name.lower()
            if (
                normalized_search in account_bank_lower
                or account_bank_lower in normalized_search
                or bank_name_lower in account_bank_lower
            ):
                return account

        return None
