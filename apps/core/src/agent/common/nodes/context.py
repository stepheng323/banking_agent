"""Shared context loading node for all flows."""

from typing import Any, TypeVar, cast

from shared.database import Account
from shared.database.models import Beneficiary
from shared.utils.serialization import sqlalchemy_to_dict


StateType = TypeVar('StateType')


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
    state_dict = cast(dict[str, Any], state)
    phone = state_dict["phone_number"]
    ctx = await user_cache.get(phone) or {}
    profile = ctx.get("profile") or {}
    accounts = ctx.get("accounts") or []
    beneficiaries_list = ctx.get("beneficiaries") or []

    print(f"[CONTEXT] load_user_context_shared: phone={phone}, accounts_from_cache={len(accounts)}, beneficiaries_from_cache={len(beneficiaries_list)}")

    user_id = profile.get("id") if isinstance(profile, dict) else None
    if user_id:
        print(f"[CONTEXT] User ID found: {user_id}, loading from database if needed")
        try:
            if not beneficiaries_list:
                print(f"[CONTEXT] Loading beneficiaries from database for user_id={user_id}, type={beneficiary_type}")
                try:
                    beneficiaries_list = beneficiary_repo.get_by_user(
                        str(user_id), beneficiary_type=beneficiary_type
                    )
                except Exception as ben_error:
                    error_msg = str(ben_error).lower()
                    if "beneficiary_type" in error_msg and ("does not exist" in error_msg or "undefinedcolumn" in error_msg):
                        print(f"[CONTEXT] ⚠️  beneficiary_type column not found, loading all beneficiaries without type filter")
                        beneficiaries_list = beneficiary_repo.get_by_user(str(user_id), beneficiary_type=None)
                    else:
                        raise
                print(f"[CONTEXT] Loaded {len(beneficiaries_list)} beneficiaries from database")
            else:
                print(f"[CONTEXT] Using {len(beneficiaries_list)} beneficiaries from cache")
                
            if not accounts:
                print(f"[CONTEXT] Loading accounts from database for user_id={user_id}")
                db_accounts = account_repo.get_by_user(str(user_id))
                accounts = db_accounts or []
                print(f"[CONTEXT] Loaded {len(accounts)} accounts from database")
            else:
                print(f"[CONTEXT] Using {len(accounts)} accounts from cache")
        except Exception as e:
            print(f"[CONTEXT] ❌ Error loading data from database: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    else:
        print(f"[CONTEXT] ⚠️  No user_id found in profile, cannot load from database")

    accounts_dict = [
        sqlalchemy_to_dict(acc) if isinstance(acc, Account) else acc
        for acc in accounts
    ]
    beneficiaries_dict = [
        sqlalchemy_to_dict(b) if isinstance(b, Beneficiary) else b
        for b in beneficiaries_list
    ]

    print(f"[CONTEXT] Final state: accounts={len(accounts_dict)}, beneficiaries={len(beneficiaries_dict)}")

    new_state: dict[str, Any] = {
        **state_dict,
        "user_profile": profile,
        "accounts": accounts_dict,
        "beneficiaries": beneficiaries_dict,
    }

    return cast(StateType, new_state)
