"""Shared account selection node for all flows."""

from typing import Callable, Optional, TypeVar, Dict, cast, TYPE_CHECKING

from apps.core.src.agent.tools.account_selection.service import AccountSelectionService

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.transfer.state import TransferState
    from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

# TypeVar without bound - bound is only for type checking and doesn't affect runtime
# Type checkers will still validate based on usage in function signatures
StateType = TypeVar('StateType')


async def select_source_account_shared(
    state: StateType,
    validator: Optional[Callable[[StateType, Dict],
                                 Optional[StateType]]] = None,
) -> StateType:
    """
    Shared account selection node for all flows.

    Args:
        state: Flow state (TransferState, AirtimeState, etc.)
        validator: Optional flow-specific validator function that takes (state, selected_account)
                   and returns updated state if validation fails, or None if validation passes
    """
    accounts = state.get("accounts", [])
    profile = state.get("user_profile", {})
    source_account_id = state.get("source_account_id")
    llm_reply = state.get("llm_reply")

    selected, response = AccountSelectionService.select_account(
        accounts=accounts,
        profile=profile or {},
        source_account_id=source_account_id,
        llm_reply=llm_reply,
    )

    if selected is not None:
        if validator:
            validation_result = validator(state, selected)
            if validation_result is not None:
                return validation_result

        return cast(StateType, {
            **state,
            "selected_source_account": selected,
            "response": "",
        })

    # If no accounts available, set error state with clear message
    if not accounts:
        error_message = "I couldn't find any account on your profile. Please add an account first to proceed with this transaction."
        return cast(StateType, {
            **state,
            "flow_state": "error",
            "response": error_message,
        })

    # If accounts exist but none selected, ask user to select
    return cast(StateType, {
        **state,
        "flow_state": "selecting_account",
        "response": response or "Please select an account.",
    })
