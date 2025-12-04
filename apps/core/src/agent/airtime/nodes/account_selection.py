"""Account selection node for airtime purchase flow."""

from apps.core.src.agent.nodes.account_selection import select_source_account_shared
from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def select_source_account(state: AirtimeState) -> AirtimeState:
    """Select source account for airtime purchase flow."""
    accounts = state.get("accounts", [])
    source_account_id = state.get("source_account_id")
    debug_log(
        f"DEBUG select_source_account: accounts={len(accounts)}, source_account_id={source_account_id}")
    
    result = await select_source_account_shared(state, validator=None)
    
    selected = result.get("selected_source_account")
    debug_log(
        f"DEBUG select_source_account: selected={selected is not None}, response={bool(result.get('response'))}")
    
    return result

