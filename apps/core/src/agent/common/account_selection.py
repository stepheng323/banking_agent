"""Shared account selection helpers for transfer/airtime/data flows."""

from typing import Dict, List, Optional


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
