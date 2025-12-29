"""Account selection node for airtime purchase flow."""

from apps.core.src.agent.tools.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from apps.core.src.agent.tools.account_selection.mandate_validator import validate_mandate_status
from apps.core.src.agent.tools.account_selection.node import select_source_account_shared

from ..graph.utils import debug_log


async def _validate_airtime_account(state: AirtimeState, selected: dict) -> AirtimeState | None:
    """
    Validate mandate status for airtime purchases.
    Blocks transactions if account mandate is not ready.
    """
    is_valid, error, _ = validate_mandate_status(selected)
    if not is_valid:
        synthesizer = get_synthesizer()
        context = build_response_context(
            ResponseIntent.MANDATE_REQUIRED, state, error_message=error
        )
        response = await synthesizer.synthesize(context)
        return {
            **state,
            "selected_source_account": selected,
            "response": response,
            "llm_reply": None,
        }
    return None


async def select_source_account(state: AirtimeState) -> AirtimeState:
    """Select source account for airtime purchase flow."""
    accounts = state.get("accounts", [])
    source_account_id = state.get("source_account_id")
    debug_log(
        f"DEBUG select_source_account: accounts={len(accounts)}, source_account_id={source_account_id}"
    )

    result = await select_source_account_shared(state, validator=_validate_airtime_account)

    selected = result.get("selected_source_account")
    debug_log(
        f"DEBUG select_source_account: selected={selected is not None}, response={bool(result.get('response'))}"
    )

    return result
