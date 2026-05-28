"""Standalone account selection module for transfer/airtime/data flows."""

from shared.formatters.accounts import format_accounts_list
from shared.i18n.renderer import render_message
from shared.utils.bank_aliases import find_matching_bank_name
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def find_account_by_bank_name(accounts: list[dict], bank_name: str) -> dict | None:
    """
    Find an account by bank name using fuzzy matching.
    """
    if not accounts or not bank_name:
        return None

    # Map bank_name -> account object for easy lookup later
    bank_map = {}
    available_names = []

    for acc in accounts:
        b_name = acc.get("bank_name")
        if b_name:
            available_names.append(b_name)
            # Store lowercased key for quick retrieval
            bank_map[b_name.lower().strip()] = acc

    # Use centralized fuzzy matching
    matched_name = find_matching_bank_name(bank_name, available_names)

    if matched_name:
        return bank_map.get(matched_name.lower().strip())

    return None


def _pick_source_account(
    accounts: list[dict],
    profile: dict,
    source_bank_name: str | None = None,
) -> dict | None:
    """
    Internal helper: Implement the priority logic for picking an account.
    """
    if not accounts:
        return None

    # 2. Bank name match (Fuzzy)
    if source_bank_name:
        matched = find_account_by_bank_name(accounts, source_bank_name)
        if matched:
            return matched

    # 3. Explicit Default
    for a in accounts:
        if a.get("is_default") is True:
            return a

    # 4. Profile Default (Legacy)
    default_id = profile.get("default_account_id")
    if default_id:
        for a in accounts:
            if str(a.get("id")) == str(default_id):
                return a

    # 5. Single Account Auto-Select
    if len(accounts) == 1:
        return accounts[0]

    return None


def select_source_account(
    accounts: list[dict],
    profile: dict,
    source_bank_name: str | None = None,
    llm_reply: str | None = None,
    locale: str = "en",
) -> tuple[dict | None, str | None]:
    """
    Select a source account from available accounts.

    Args:
        accounts: List of account dictionaries
        profile: User profile dictionary
        source_bank_name: Bank name to find account by (e.g., 'Access', 'GTB')
        llm_reply: Optional LLM-generated reply to include in response

    Returns:
        Tuple of (selected_account, response_message)
        - If account selected: (account_dict, None)
        - If needs user input: (None, response_message)
    """
    if not accounts:
        return (
            None,
            llm_reply or render_message("source_account.profile_no_accounts", locale),
        )

    selected = _pick_source_account(accounts, profile or {}, source_bank_name)

    if selected is not None:
        return selected, None

    # If no account selected, return formatted list for user to choose
    account_list = format_accounts_list(accounts, locale=locale)
    logger.debug("debug_accounts")

    if llm_reply:
        combined = f"{llm_reply}\n\n{account_list}"
        logger.debug("debug_combined_response")
        return None, combined
    return None, account_list
