"""Standalone account selection service for transfer/airtime/data flows."""

from typing import Dict, List, Optional, Tuple

from shared.formatters.accounts import format_accounts_list
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountSelectionService:
    """Service for selecting source accounts across different flows."""

    @staticmethod
    def find_account_by_bank_name(
        accounts: List[Dict], bank_name: str
    ) -> Optional[Dict]:
        """
        Find an account by bank name using fuzzy matching.
        
        Args:
            accounts: List of account dictionaries
            bank_name: Bank name or abbreviation (e.g., 'GTB', 'Access', 'UBA')
            
        Returns:
            Matching account dict or None
        """
        from shared.utils.bank_aliases import normalize_bank_name
        
        if not accounts or not bank_name:
            return None
        
        bank_name_lower = bank_name.lower().strip()
        normalized_search = normalize_bank_name(bank_name)
        
        for account in accounts:
            account_bank = account.get("bank_name", "")
            if not account_bank:
                continue
            account_bank_lower = account_bank.lower()
            if (normalized_search in account_bank_lower or 
                account_bank_lower in normalized_search or
                bank_name_lower in account_bank_lower):
                return account
        
        return None

    @staticmethod
    def pick_source_account(
        accounts: List[Dict], profile: Dict, source_account_id: Optional[str],
        source_bank_name: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Pick a source account from the list of accounts.
        
        Args:
            accounts: List of account dictionaries
            profile: User profile dictionary
            source_account_id: Explicit account ID to use
            source_bank_name: Bank name to find account by (e.g., 'Access', 'GTB')
            
        Returns:
            Selected account dict or None
        """
        if not accounts:
            return None
        if source_account_id:
            for a in accounts:
                if str(a.get("id")) == str(source_account_id):
                    return a
        if source_bank_name:
            matched = AccountSelectionService.find_account_by_bank_name(accounts, source_bank_name)
            if matched:
                return matched
        default_id = profile.get("default_account_id")
        if default_id:
            for a in accounts:
                if str(a.get("id")) == str(default_id):
                    return a
        if len(accounts) == 1:
            return accounts[0]
        return None

    @staticmethod
    def select_account(
        accounts: List[Dict],
        profile: Dict,
        source_account_id: Optional[str] = None,
        source_bank_name: Optional[str] = None,
        llm_reply: Optional[str] = None,
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Select a source account from available accounts.
        
        Args:
            accounts: List of account dictionaries
            profile: User profile dictionary
            source_account_id: Explicitly requested account ID
            source_bank_name: Bank name to find account by (e.g., 'Access', 'GTB')
            llm_reply: Optional LLM-generated reply to include in response
            
        Returns:
            Tuple of (selected_account, response_message)
            - If account selected: (account_dict, None)
            - If needs user input: (None, response_message)
        """
        if not accounts:
            return None, llm_reply or "I couldn't find any account on your profile. Please add an account first."
        
        selected = AccountSelectionService.pick_source_account(
            accounts, profile or {}, source_account_id, source_bank_name
        )
        
        if selected is not None:
            return selected, None
        
        account_list = format_accounts_list(accounts)
        logger.debug("debug_accounts")
        
        if llm_reply:
            combined = f"{llm_reply}\n\n{account_list}"
            logger.debug("debug_combined_response")
            return None, combined
        return None, account_list
