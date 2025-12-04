"""Service for managing user bank accounts."""

from typing import List, Optional, Dict, Any
from shared.repositories.account_repository import AccountRepository
from shared.database.models import Account


class AccountManagementService:
    """Service for managing user bank accounts (link, unlink, list, set default)."""
    
    def __init__(self, account_repo: AccountRepository):
        """
        Initialize account management service.
        
        Args:
            account_repo: Repository for account operations
        """
        self.account_repo = account_repo
    
    async def list_accounts(self, user_id: str) -> str:
        """
        List all linked accounts for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Formatted message with account list
        """
        accounts = self.account_repo.get_by_user(user_id)
        
        if not accounts:
            return (
                "You don't have any linked bank accounts yet.\n\n"
                "To link an account, I'll need to guide you through Mono Connect. "
                "This is currently done during onboarding, but we can set it up for you again."
            )
        
        lines = ["🏦 *Your Linked Accounts:*\n"]
        for i, account in enumerate(accounts, 1):
            default_marker = " ✓ *Default*" if account.is_default else ""
            masked_number = f"***{account.account_number[-4:]}" if account.account_number else "****"
            
            lines.append(
                f"{i}. {account.bank_name} ({masked_number}){default_marker}\n"
                f"   {account.account_name or 'Account'}"
            )
        
        lines.append(
            "\n\n💡 *Tips:*\n"
            "• Reply with a number (1, 2, etc.) to set that as your default account\n"
            "• Say 'unlink account [number]' to remove an account\n"
            "• Say 'link new account' to add another account"
        )
        
        return "\n".join(lines)
    
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
        
        # Try to parse as index first
        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            # Not a number, try to match by bank name
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)
        
        if not selected_account:
            return (
                f"I couldn't find an account matching '{account_identifier}'.\n\n"
                f"You have {len(accounts)} linked account(s). "
                f"Please use a number (1-{len(accounts)}) or a bank name like 'GTB', 'UBA', 'Access', etc."
            )
        
        try:
            self.account_repo.set_default_account(user_id, selected_account.account_id)
            
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
        
        # Try to parse as index first
        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            # Not a number, try to match by bank name
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)
        
        if not selected_account:
            return (
                f"I couldn't find an account matching '{account_identifier}'.\n\n"
                f"You have {len(accounts)} linked account(s). "
                f"Please use a number (1-{len(accounts)}) or a bank name like 'GTB', 'UBA', 'Access', etc."
            )
        
        try:
            success = self.account_repo.delete_account(
                selected_account.account_id,
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
        
        # Common bank abbreviations
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
        
        # Normalize the search term
        normalized_search = bank_aliases.get(bank_name_lower, bank_name_lower)
        
        # Try exact match first
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
