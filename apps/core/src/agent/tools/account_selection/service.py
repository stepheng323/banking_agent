"""Standalone account selection service for transfer/airtime/data flows."""

from typing import Dict, List, Optional, Tuple

from shared.formatters.accounts import format_accounts_list


class AccountSelectionService:
    """Service for selecting source accounts across different flows."""

    @staticmethod
    def pick_source_account(
        accounts: List[Dict], profile: Dict, source_account_id: Optional[str]
    ) -> Optional[Dict]:
        """Pick a source account from the list of accounts."""
        if not accounts:
            return None
        if source_account_id:
            for a in accounts:
                if str(a.get("id")) == str(source_account_id):
                    return a
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
        
        selected = AccountSelectionService.pick_source_account(accounts, profile or {}, source_account_id)
        
        if selected is not None:
            return selected, None
        
        account_list = format_accounts_list(accounts)
        print(f"DEBUG AccountSelectionService: accounts count={len(accounts)}, formatted_list={account_list[:100] if account_list else 'EMPTY'}")
        
        if llm_reply:
            combined = f"{llm_reply}\n\n{account_list}"
            print(f"DEBUG AccountSelectionService: Combined response length={len(combined)}")
            return None, combined
        return None, account_list
