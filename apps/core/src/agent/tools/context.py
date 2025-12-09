"""Shared context loading node for all flows."""

import asyncio
from typing import Any, TypeVar, cast

from shared.database import Account
from shared.database.models import Beneficiary
from shared.utils.serialization import sqlalchemy_to_dict
from shared.utils.logging import get_logger

logger = get_logger(__name__)


StateType = TypeVar('StateType')


async def load_user_context_shared(
    state: StateType,
    user_cache: Any,  # UserDataCache
    account_repo: Any,
    beneficiary_repo: Any,
    beneficiary_type: str = "transfer",
) -> StateType:
    """
    Shared context loading node for all flows.

    Args:
        state: Flow state (TransferState, AirtimeState, etc.)
        user_cache: UserDataCache service
        account_repo: Account repository
        beneficiary_repo: Beneficiary repository
        beneficiary_type: Type of beneficiaries to load ("transfer" or "airtime")

    Returns:
        Updated state with user profile, accounts, and beneficiaries
    """
    state_dict = cast(dict[str, Any], state)
    phone = state_dict["phone_number"]
    
    # Load from UserDataCache
    cached = await user_cache.get_all_user_data(phone)
    profile = cached.get("profile") or {}
    accounts = cached.get("accounts") or []
    beneficiaries_list = cached.get("beneficiaries") or []

    logger.info("log_event")

    user_id = profile.get("id") if isinstance(profile, dict) else None
    if user_id:
        logger.info("user_id")
        
        # Determine what needs to be loaded
        need_beneficiaries = not beneficiaries_list
        need_accounts = not accounts
        
        # If both need loading, fetch in parallel for better performance
        if need_beneficiaries and need_accounts:
            logger.info("loading_both_beneficiaries_and")
            
            def load_beneficiaries_sync():
                """Load beneficiaries from database (sync)."""
                try:
                    return beneficiary_repo.get_by_user(
                        str(user_id), beneficiary_type=beneficiary_type
                    )
                except Exception as ben_error:
                    error_msg = str(ben_error).lower()
                    if "beneficiary_type" in error_msg and ("does not exist" in error_msg or "undefinedcolumn" in error_msg):
                        logger.warning("column_not")
                        return beneficiary_repo.get_by_user(str(user_id), beneficiary_type=None)
                    else:
                        raise
            
            def load_accounts_sync():
                """Load accounts from database (sync)."""
                return account_repo.get_by_user(str(user_id))
            
            # Run both queries in parallel using thread pool for sync DB calls
            try:
                beneficiaries_task = asyncio.create_task(
                    asyncio.to_thread(load_beneficiaries_sync)
                )
                accounts_task = asyncio.create_task(
                    asyncio.to_thread(load_accounts_sync)
                )
                
                beneficiaries_result, accounts_result = await asyncio.gather(
                    beneficiaries_task, accounts_task, return_exceptions=True
                )
                
                # Handle beneficiaries result
                if isinstance(beneficiaries_result, Exception):
                    logger.error("error_loading")
                    beneficiaries_list = []
                else:
                    beneficiaries_list = beneficiaries_result or []
                    logger.info("loaded_beneficiaries_from")
                
                # Handle accounts result
                if isinstance(accounts_result, Exception):
                    logger.error("error_loading")
                    accounts = []
                else:
                    accounts = accounts_result or []
                    logger.info("loaded_accounts_from")
                    
            except Exception as e:
                logger.error("error_in_parallel")
                import traceback
                traceback.print_exc()
                # Fallback to empty lists
                if need_beneficiaries:
                    beneficiaries_list = []
                if need_accounts:
                    accounts = []
        
        else:
            # Load sequentially if only one needs loading
            try:
                if need_beneficiaries:
                    logger.info("loading_beneficiaries_from_database")
                    try:
                        beneficiaries_list = beneficiary_repo.get_by_user(
                            str(user_id), beneficiary_type=beneficiary_type
                        )
                    except Exception as ben_error:
                        error_msg = str(ben_error).lower()
                        if "beneficiary_type" in error_msg and ("does not exist" in error_msg or "undefinedcolumn" in error_msg):
                            logger.warning("column_not")
                            beneficiaries_list = beneficiary_repo.get_by_user(str(user_id), beneficiary_type=None)
                        else:
                            raise
                    logger.info("loaded_beneficiaries_from")
                else:
                    logger.info("using_beneficiaries_from")
                    
                if need_accounts:
                    logger.info("loading_accounts_from_database")
                    db_accounts = account_repo.get_by_user(str(user_id))
                    accounts = db_accounts or []
                    logger.info("loaded_accounts_from")
                else:
                    logger.info("using_accounts_from")
            except Exception as e:
                logger.error("error_loading_data_from")
                import traceback
                traceback.print_exc()
    else:
        logger.warning("no_found_in")

    accounts_dict = [
        sqlalchemy_to_dict(acc) if isinstance(acc, Account) else acc
        for acc in accounts
    ]
    beneficiaries_dict = [
        sqlalchemy_to_dict(b) if isinstance(b, Beneficiary) else b
        for b in beneficiaries_list
    ]
    
    # Cache the loaded data
    if beneficiaries_dict:
        await user_cache.set_beneficiaries(phone, beneficiaries_dict)
    if accounts_dict:
        await user_cache.set_accounts(phone, accounts_dict)

    logger.info("final")

    new_state: dict[str, Any] = {
        **state_dict,
        "user_profile": profile,
        "accounts": accounts_dict,
        "beneficiaries": beneficiaries_dict,
    }

    return cast(StateType, new_state)
