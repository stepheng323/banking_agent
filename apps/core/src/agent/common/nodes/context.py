"""Shared context loading node for all flows."""

from typing import Any, TypeVar, cast

from shared.database import Account
from shared.database.models import Beneficiary
from shared.utils.serialization import sqlalchemy_to_dict

from apps.core.src.agent.transfer.state import TransferState
from apps.core.src.agent.airtime.state import AirtimeState

StateType = TypeVar('StateType', bound=TransferState | AirtimeState)


async def load_user_context_shared(
    state: StateType,
    user_cache: Any,
    account_repo: Any,
    beneficiary_repo: Any,
    beneficiary_type: str = "transfer",
) -> StateType:
    """
    Shared context loading node for all flows.

    Args:
        state: Flow state (TransferState, AirtimeState, etc.)
        user_cache: User context cache service
        account_repo: Account repository
        beneficiary_repo: Beneficiary repository
        beneficiary_type: Type of beneficiaries to load ("transfer" or "airtime")

    Returns:
        Updated state with user profile, accounts, and beneficiaries
    """
    phone = state["phone_number"]
    ctx = await user_cache.get(phone) or {}
    profile = ctx.get("profile") or {}
    accounts = ctx.get("accounts") or []
    beneficiaries_list = ctx.get("beneficiaries") or []

    user_id = profile.get("id") if isinstance(profile, dict) else None
    if user_id:
        try:
            if not beneficiaries_list:
                beneficiaries_list = beneficiary_repo.get_by_user(
                    str(user_id), beneficiary_type=beneficiary_type
                )
            if not accounts:
                db_accounts = account_repo.get_by_user(str(user_id))
                accounts = db_accounts or []
        except Exception:
            pass

    accounts_dict = [
        sqlalchemy_to_dict(acc) if isinstance(acc, Account) else acc
        for acc in accounts
    ]
    beneficiaries_dict = [
        sqlalchemy_to_dict(b) if isinstance(b, Beneficiary) else b
        for b in beneficiaries_list
    ]

    new_state = dict(state)
    new_state.update({
        "user_profile": profile,
        "accounts": accounts_dict,
        "beneficiaries": beneficiaries_dict,
    })

    return cast(StateType, new_state)
