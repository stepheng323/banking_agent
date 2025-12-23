"""Service for managing user bank accounts."""

import asyncio
from typing import List, Optional, Dict, Any
import time
from langchain_openai import ChatOpenAI
from shared.clients.whatsapp.client import WhatsAppClient
from shared.clients.providers.mono import mono_client
from shared.cache.user_data import UserDataCache
from shared.config import settings
from shared.repositories.account_repository import AccountRepository
from shared.repositories.user_repository import UserRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.models.account import Account
from shared.database.models import User
from shared.utils.logging import get_logger
from apps.core.src.agent.sub_agents.account_management.parser import AccountManagementParser, AccountManagementIntent
from apps.core.src.agent.sub_agents.account_management.formatter import AccountManagementFormatter
from shared.services.onboarding import bvn_service

logger = get_logger(__name__)


class AccountManagementService:
    """Service for managing user bank accounts (link, unlink, list, set default)."""
    
    def __init__(
        self, 
        account_repo: AccountRepository, 
        user_repo: UserRepository,
        llm: ChatOpenAI,
        whatsapp_client: WhatsAppClient
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
        self.whatsapp_client = whatsapp_client
        self.parser = AccountManagementParser(llm)
    
    async def handle_account_management(
        self,
        phone_number: str,
        text: str,
        user_ctx: Dict[str, Any]
    ) -> str:
        """
        Handle account management intent.
        
        Args:
            phone_number: User's phone number
            text: User's command text
            user_ctx: User context
            
        Returns:
            Response message
        """
        profile = user_ctx.get("profile")
        if not profile:
            return "User not found."
        user_id = str(profile["id"])
        
        parsed: AccountManagementIntent = await self.parser.parse(text)
        action = parsed.action
        identifier = parsed.identifier
        
        response = ""
        if action == "unlink":
            if identifier:
                response = await self.unlink_account(user_id, identifier)
            else:
                response = "Which account would you like to unlink? Please say 'unlink [bank name]' or 'unlink [number]'."

        elif action == "set_default":
            if identifier:
                response = await self.set_default(user_id, identifier)
            else:
                response = "Which account should be your default? Say 'set [bank name] as default'."
            
        elif action == "link":
            response = await self.link_account(phone_number)
                 
        elif action == "list":
            accounts = user_ctx.get("accounts")
            if accounts:
                response = AccountManagementFormatter.format_account_list(accounts)
            else:
                response = await self.list_accounts(user_id)
            
        else:
            accounts = user_ctx.get("accounts")
            if accounts:
                response = AccountManagementFormatter.format_account_list(accounts)
            else:
                response = await self.list_accounts(user_id)
                
        language = user_ctx.get("language")
        if language and language.lower() not in ("english", "en"):
            return await self._translate_response(response, language)
            
        return response

    async def _translate_response(self, text: str, language: str) -> str:
        """Translate response to user's preferred language using LLM."""
        try:
            prompt = (
                f"Translate the following banking assistant response to {language}. "
                "Keep the formatting (markdown, emojis) exactly the same. "
                "Adapt the tone to be natural in the target language (e.g., Use Pidgin English style if language is Pidgin).\n\n"
                f"Original Response:\n{text}"
            )
            result = await self.llm.ainvoke(prompt)
            if hasattr(result, 'content'):
                return result.content
            return str(result)
        except Exception:
            return text

    async def link_account(self, phone_number: str) -> str:
        """
        Send account linking flow to link a new account.
        
        Uses stored BVN - flow starts at METHOD_SELECTION (OTP verification).
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Instruction message
        """
        flow_id = settings.account_linking_flow_id
    
        if not flow_id:
            return "Sorry, account linking is temporarily unavailable. Please contact support."
        
        timestamp = int(time.time())
        flow_token = f"link-{phone_number}-{timestamp}"
        
        result = await bvn_service.initiate_account_linking(flow_token, phone_number)
        
        if not result["success"]:
            error_msg = result.get("error", "Failed to start account linking.")
            logger.error("account_linking_init_failed", phone=phone_number, error=error_msg)
            return error_msg
        
        linking_data = result.get("data", {})
        methods = linking_data.get("methods", [])
        bvn = linking_data.get("bvn", "")
        
        await self.whatsapp_client.send_flow(
            to=phone_number,
            header="Link New Account",
            flow_cta="Continue",
            flow_id=flow_id,
            screen_name="METHOD_SELECTION",
            flow_token=flow_token,
            text_body="Tap Continue to link a new bank account.",
            flow_action_payload={
                "screen": "METHOD_SELECTION",
                "data": {
                    "methods": methods,
                    "bvn": bvn,
                },
            },
        )
        
        return ""

    async def list_accounts(self, user_id: str) -> str:
        """
        List all linked accounts for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Formatted message with account list
        """
        accounts = self.account_repo.get_by_user(user_id)
        return AccountManagementFormatter.format_account_list(accounts)
    
    async def set_default(self, user_id: str, account_identifier: str) -> str:
        """
        Set an account as default by its index (1-based) or bank name.
        
        Args:
            user_id: User ID
            account_identifier: Account index (1, 2, etc.) or bank name (GTB, UBA, etc.)
            
        Returns:
            Success or error message
        """
        accounts = self.account_repo.get_by_user(user_id)
        
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
            with UnitOfWork() as uow:
                uow.accounts.set_default_account(user_id, str(selected_account.account_id))
                uow.commit()
            
            user = self.user_repo.get_by_id(user_id)
            if user:
                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))
            
            masked_number = f"***{selected_account.account_number[-4:]}"
            return (
                f"✓ *Default account updated!*\n\n"
                f"{selected_account.bank_name} ({masked_number}) is now your default account.\n\n"
                f"All transactions will use this account unless you specify otherwise."
            )
        except Exception as e:
            logger.error("set_default_account_error", user_id=user_id, account_id=str(selected_account.account_id), error=str(e), exc_info=True)
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
        accounts = self.account_repo.get_by_user(user_id)
        
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
            if mandate_id:
                try:
                    await mono_client.cancel_mandate(mandate_id)
                    logger.info("mandate_cancelled_for_unlink", mandate_id=mandate_id)
                except Exception as e:
                    logger.warning("cancel_mandate_failed_on_unlink", mandate_id=mandate_id, error=str(e))
            
            success = self.account_repo.delete_account(
                str(selected_account.account_id),
                user_id
            )
            
            if success:
                try:
                    with UnitOfWork() as uow:
                        if uow.users:
                            user = uow.users.get_by_id(user_id)  
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
            logger.error("unlink_account_error", user_id=user_id, account_id=str(selected_account.account_id), error=str(e), exc_info=True)
            return "Sorry, I couldn't unlink that account. Please try again."
    
    def _find_account_by_bank_name(self, accounts: List[Account], bank_name: str) -> Optional[Account]:
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
            if (normalized_search in account_bank_lower or 
                account_bank_lower in normalized_search or
                bank_name_lower in account_bank_lower):
                return account
        
        return None

