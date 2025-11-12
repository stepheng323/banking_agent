"""Standalone account selection service for transfer/airtime/data flows."""

from typing import Dict, List, Optional, Tuple

from shared.formatters.accounts import format_accounts_list
from apps.core.src.agent.common.account_selection import pick_source_account


class AccountSelectionService:
    """Service for selecting source accounts across different flows."""

    @staticmethod
    def select_account(
        accounts: List[Dict],
        profile: Dict,
        source_account_id: Optional[str] = None,
        llm_reply: Optional[str] = None,
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Select a source account from available accounts.
        
        Args:
            accounts: List of account dictionaries
            profile: User profile dictionary
            source_account_id: Explicitly requested account ID
            llm_reply: Optional LLM-generated reply to include in response
            
        Returns:
            Tuple of (selected_account, response_message)
            - If account selected: (account_dict, None)
            - If needs user input: (None, response_message)
        """
        if not accounts:
            return None, llm_reply or "I couldn't find any account on your profile. Please add an account first."
        
        selected = pick_source_account(accounts, profile or {}, source_account_id)
        
        if selected is not None:
            return selected, None
        
        # Need to ask user to select
        account_list = format_accounts_list(accounts)
        print(f"DEBUG AccountSelectionService: accounts count={len(accounts)}, formatted_list={account_list[:100] if account_list else 'EMPTY'}")
        
        if llm_reply:
            combined = f"{llm_reply}\n\n{account_list}"
            print(f"DEBUG AccountSelectionService: Combined response length={len(combined)}")
            return None, combined
        return None, account_list

