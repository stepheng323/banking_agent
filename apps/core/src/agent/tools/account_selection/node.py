"""Shared account selection node for all flows."""

import asyncio
import inspect
from typing import Callable, Optional, TypeVar, Dict, cast, TYPE_CHECKING, Any, Coroutine, Union

from apps.core.src.agent.tools.account_selection.service import AccountSelectionService

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.transfer.state import TransferState
    from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

StateType = TypeVar('StateType')

# Validator can be sync or async
ValidatorFunc = Callable[[StateType, Dict], Union[Optional[StateType], Coroutine[Any, Any, Optional[StateType]]]]


async def select_source_account_shared(
    state: StateType,
    validator: Optional[ValidatorFunc] = None,
) -> StateType:
    """
    Shared account selection node for all flows.

    Args:
        state: Flow state (TransferState, AirtimeState, etc.)
        validator: Optional flow-specific validator function that takes (state, selected_account)
                   and returns updated state if validation fails, or None if validation passes.
                   Can be sync or async.
    """
    accounts = state.get("accounts", [])
    profile = state.get("user_profile", {})
    source_account_id = state.get("source_account_id")
    source_bank_name = state.get("source_bank_name")
    llm_reply = state.get("llm_reply")

    selected, response = AccountSelectionService.select_account(
        accounts=accounts,
        profile=profile or {},
        source_account_id=source_account_id,
        source_bank_name=source_bank_name,
        llm_reply=llm_reply,
    )

    if selected is not None:
        if validator:
            # Handle both sync and async validators
            result = validator(state, selected)
            if asyncio.iscoroutine(result):
                validation_result = await result
            else:
                validation_result = result
            if validation_result is not None:
                return validation_result

        return cast(StateType, {
            **state,
            "selected_source_account": selected,
            "response": "",
        })

    if not accounts:
        error_message = "I couldn't find any account on your profile. Please add an account first to proceed with this transaction."
        return cast(StateType, {
            **state,
            "flow_state": "error",
            "response": error_message,
        })

    return cast(StateType, {
        **state,
        "flow_state": "selecting_account",
        "response": response or "Please select an account.",
    })
