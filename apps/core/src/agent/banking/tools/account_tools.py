# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""
Banking tools for account operations.
"""

import asyncio
from typing import Optional, Dict, Any, List, TypedDict, Union
from langchain.tools import tool


class AccountDict(TypedDict):
    """Account information dictionary."""
    id: str
    account_id: str
    account_number: str
    account_name: str
    bank_name: str
    bank_code: str
    balance: float
    account_type: str
    currency: str
    is_active: bool


class UserAccountsSuccess(TypedDict):
    """Success response for get_user_accounts."""
    success: bool
    accounts: List[AccountDict]
    total_accounts: int


class UserAccountsError(TypedDict):
    """Error response for get_user_accounts."""
    success: bool
    error: str
    accounts: List[AccountDict]


# Try to import payment provider factory, but don't fail if not available
try:
    from shared.clients.payment_provider_factory import PaymentProviderFactory
    PAYMENT_SERVICE_AVAILABLE = True
except (ImportError, ValueError):
    PAYMENT_SERVICE_AVAILABLE = False
    PaymentProviderFactory = None


@tool
def get_user_accounts(phone_number: str) -> Union[UserAccountsSuccess, UserAccountsError]:
    """
    Get all active bank accounts for a user.

    Args:
        phone_number: User's phone number (e.g., "+2348012345678")

    Returns:
        UserAccountsSuccess: On success, containing:
            - success: True
            - accounts: List[AccountDict] - List of account dictionaries
            - total_accounts: int - Total number of accounts

    """
    # TODO: Replace with actual database implementation
    # from shared.repositories.account_repository import AccountRepository
    # from shared.repositories.user_repository import UserRepository
    # from shared.database.connection import get_db_session

    try:
        # Placeholder implementation
        return {
            "success": True,
            "accounts": [
                {
                    "id": "acc_001",
                    "account_id": "acc_001",
                    "account_number": "0123456789",
                    "account_name": "John Doe",
                    "bank_name": "FirstBank",
                    "bank_code": "011",
                    "balance": 25000.00,
                    "account_type": "savings",
                    "currency": "NGN",
                    "is_active": True
                },
                {
                    "id": "acc_002",
                    "account_id": "acc_002",
                    "account_number": "9876543210",
                    "account_name": "John Doe",
                    "bank_name": "GTBank",
                    "bank_code": "058",
                    "balance": 150000.00,
                    "account_type": "current",
                    "currency": "NGN",
                    "is_active": True
                }
            ],
            "total_accounts": 2
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to fetch accounts: {str(e)}",
            "accounts": []
        }


@tool
def get_account_balance(
    phone_number: str,
    account_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get balance for a specific account or all accounts.

    Args:
        phone_number: User's phone number
        account_id: Optional specific account ID. If None, returns all balances.

    Returns:
        Dictionary containing balance information

    Example:
        >>> get_account_balance("+2348012345678", "acc_001")
        {"success": True, "balance": 25000.00, "account_number": "0123456789"}
    """
    try:
        accounts_result = get_user_accounts.invoke(
            {"phone_number": phone_number})

        if not accounts_result["success"]:
            return {
                "success": False,
                "error": "Could not fetch accounts"
            }

        accounts = accounts_result["accounts"]

        if account_id:
            account = next(
                (a for a in accounts if a["id"] == account_id), None)
            if not account:
                return {
                    "success": False,
                    "error": f"Account {account_id} not found"
                }

            return {
                "success": True,
                "account_id": account_id,
                "account_number": account["account_number"],
                "bank_name": account["bank_name"],
                "balance": account["balance"],
                "currency": account["currency"],
                "account_type": account["account_type"]
            }

        total_balance = sum(a["balance"] for a in accounts)

        return {
            "success": True,
            "accounts": [
                {
                    "id": a["id"],
                    "account_number": a["account_number"],
                    "bank_name": a["bank_name"],
                    "balance": a["balance"],
                    "currency": a["currency"]
                }
                for a in accounts
            ],
            "total_balance": total_balance,
            "currency": "NGN"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get balance: {str(e)}"
        }


@tool
def get_account_statement(
    phone_number: str,
    account_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Get account statement/transaction history.

    Args:
        phone_number: User's phone number
        account_id: Account ID
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
        limit: Maximum number of transactions to return (default 10)

    Returns:
        Dictionary with transaction history
    """
    # TODO: Implement actual statement retrieval

    try:
        return {
            "success": True,
            "account_id": account_id,
            "transactions": [
                {
                    "id": "txn_001",
                    "type": "credit",
                    "amount": 5000.00,
                    "description": "Transfer from Jane Doe",
                    "date": "2025-10-28T10:30:00",
                    "balance_after": 30000.00
                },
                {
                    "id": "txn_002",
                    "type": "debit",
                    "amount": 2000.00,
                    "description": "ATM Withdrawal",
                    "date": "2025-10-27T15:45:00",
                    "balance_after": 25000.00
                }
            ],
            "start_date": start_date,
            "end_date": end_date,
            "count": 2
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get statement: {str(e)}"
        }


@tool
def verify_account_number(
    account_number: str,
    bank_code: str
) -> Dict[str, Any]:
    """
    Verify and get account name for an account number using payment service provider.

    Uses the best available payment provider (Flutterwave, Paystack, etc.) to resolve
    bank account details and verify the account holder name.

    Args:
        account_number: 10-digit account number
        bank_code: Bank code (e.g., "058" for GTBank, "011" for First Bank)

    Returns:
        Dictionary with:
            - success: bool
            - account_name: str (verified account holder name) if successful
            - account_number: str
            - bank_code: str
            - bank_name: str ("Verified")
            - error: str (if failed)
    """
    if not PAYMENT_SERVICE_AVAILABLE or not PaymentProviderFactory:
        return {
            "success": False,
            "error": "Payment service not available",
            "account_number": account_number,
            "bank_code": bank_code,
        }

    try:
        provider = PaymentProviderFactory.get_provider_for_service(
            "resolve_account")

        if not provider:
            return {
                "success": False,
                "error": "No payment provider available for account resolution",
                "account_number": account_number,
                "bank_code": bank_code,
            }

        result = asyncio.run(
            provider.resolve_account(account_number, bank_code))

        if result.get("success"):
            return {
                "success": True,
                "account_number": result.get("account_number", account_number),
                "account_name": result.get("account_name", ""),
                "bank_code": result.get("bank_code", bank_code),
                "bank_name": "Verified",
                "provider": result.get("provider", "unknown"),
            }

        return {
            "success": False,
            "error": result.get("error", "Verification failed"),
            "account_number": account_number,
            "bank_code": bank_code,
            "provider": result.get("provider"),
        }

    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "account_number": account_number,
            "bank_code": bank_code,
        }
    except Exception as e:
        print(f"⚠️  Account resolution failed: {e}")
        return {
            "success": False,
            "error": f"Account resolution service error: {str(e)}",
            "account_number": account_number,
            "bank_code": bank_code,
        }
