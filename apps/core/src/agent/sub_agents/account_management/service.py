"""Service for managing user bank accounts."""

from typing import List, Optional, Dict, Any
import time
from langchain_openai import ChatOpenAI
from shared.clients.whatsapp_client import WhatsAppClient
from shared.config import settings
from shared.repositories.account_repository import AccountRepository
from shared.repositories.user_repository import UserRepository
from shared.database.models import Account
from apps.core.src.agent.sub_agents.account_management.parser import AccountManagementParser, AccountManagementIntent
from apps.core.src.agent.sub_agents.account_management.formatter import AccountManagementFormatter


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
        
        if action == "unlink":
            if identifier:
                return await self.unlink_account(user_id, identifier)
            return "Which account would you like to unlink? Please say 'unlink [bank name]' or 'unlink [number]'."

        elif action == "set_default":
            if identifier:
                return await self.set_default(user_id, identifier)
            return "Which account should be your default? Say 'set [bank name] as default'."
            
        elif action == "link":
            return await self.link_account(phone_number)
                 
        elif action == "list":
            accounts = user_ctx.get("accounts")
            if accounts:
                return AccountManagementFormatter.format_account_list(accounts)
            return await self.list_accounts(user_id)
            
        else:
            accounts = user_ctx.get("accounts")
            if accounts:
                return AccountManagementFormatter.format_account_list(accounts)
            return await self.list_accounts(user_id)

    async def link_account(self, phone_number: str) -> str:
        """
        Send Mono Connect flow to link a new account.
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Instruction message
        """
        flow_id = settings.onboarding_flow_id
        if not flow_id:
            return "Sorry, account linking is temporarily unavailable. Please contact support."
            
        timestamp = int(time.time())
        flow_token = f"link-{phone_number}-{timestamp}"
        
        await self.whatsapp_client.send_flow(
            to=phone_number,
            header="Link New Account",
            flow_cta="Link Account",
            flow_id=flow_id,
            screen_name="MonoConnect",
            flow_token=flow_token,
            text_body="Tap the button below to securely link your bank account."
        )
        
        return "I've sent you a secure link to connect your new bank account. Please tap the 'Link Account' button below to proceed."

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
            self.account_repo.set_default_account(user_id, str(selected_account.account_id))
            
            masked_number = f"***{selected_account.account_number[-4:]}"
            return (
                f"✅ *Default account updated!*\n\n"
                f"{selected_account.bank_name} ({masked_number}) is now your default account.\n\n"
                f"All transactions will use this account unless you specify otherwise."
            )
        except Exception as e:
            print(f"Error setting default account: {e}")
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
            success = self.account_repo.delete_account(
                str(selected_account.account_id),
                user_id
            )
            
            if success:
                masked_number = f"***{selected_account.account_number[-4:]}"
                return (
                    f"✅ *Account unlinked!*\n\n"
                    f"{selected_account.bank_name} ({masked_number}) has been removed from your account.\n\n"
                    f"You now have {len(accounts) - 1} linked account(s)."
                )
            else:
                return "Sorry, I couldn't unlink that account. Please try again."
        except Exception as e:
            print(f"Error unlinking account: {e}")
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
        bank_name_lower = bank_name.lower().strip()
        
        bank_aliases = {
            "gtb": "gtbank",
            "gtbank": "gtbank",
            "guaranty trust": "gtbank",
            "uba": "uba",
            "united bank": "uba",
            "access": "access",
            "zenith": "zenith",
            "first bank": "first bank",
            "firstbank": "first bank",
            "fbn": "first bank",
            "union": "union bank",
            "sterling": "sterling",
            "stanbic": "stanbic",
            "fidelity": "fidelity",
            "wema": "wema",
            "polaris": "polaris",
            "keystone": "keystone",
            "fcmb": "fcmb",
            "ecobank": "ecobank",
            "providus": "providus",
            "kuda": "kuda",
            "opay": "opay",
            "palmpay": "palmpay",
        }
        
        normalized_search = bank_aliases.get(bank_name_lower, bank_name_lower)
        
        for account in accounts:
            account_bank_lower = account.bank_name.lower()
            if (normalized_search in account_bank_lower or 
                account_bank_lower in normalized_search or
                bank_name_lower in account_bank_lower):
                return account
        
        return None

    
    async def get_default_account(self, user_id: str) -> Optional[Account]:
        """
        Get user's default account.
        
        Args:
            user_id: User ID
            
        Returns:
            Default account or None
        """
        return self.account_repo.get_default_account(user_id)
    
    async def get_accounts(self, user_id: str) -> List[Account]:
        """
        Get all accounts for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of accounts
        """
        return self.account_repo.get_by_user(user_id)
