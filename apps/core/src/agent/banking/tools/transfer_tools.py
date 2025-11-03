"""
Transfer-specific tools for the LLM-driven transfer agent.
"""
from typing import Dict, Any, List, Optional
import re
from langchain.tools import tool
from sqlalchemy.orm import Session

from apps.core.src.agent.banking.tools.account_tools import get_account_balance
from apps.core.src.agent.utils.beneficiary_matcher import match_beneficiaries
from shared.repositories import UnitOfWork
from shared.repositories.user_repository import UserRepository
from shared.repositories import BeneficiaryRepository



def _get_saved_beneficiaries(phone_number: str, db_session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Get saved beneficiaries for a user from the database.

    Args:
        phone_number: User's phone number
        db_session: Optional existing session to reuse

    Returns:
        List of beneficiary dictionaries
    """
    owns_session = db_session is None

    try:
        if owns_session:
           # Create new UnitOfWork
            with UnitOfWork() as uow:
                user = uow.users.get_by_phone(phone_number)

                if not user:
                    return []

                beneficiaries = uow.beneficiaries.get_all_for_user(
                    str(user.id))

                result = []
                for ben in beneficiaries:
                    result.append({
                        "id": str(ben.id),
                        "name": ben.account_name,
                        "nickname": ben.alias or ben.account_name,
                        "account_number": ben.account_number,
                        "bank_name": ben.bank_name,
                        "bank_code": ben.bank_code,
                        "frequency": 0  # TODO: Add frequency tracking to Beneficiary model if needed
                    })
                return result
        else:

            user_repo = UserRepository(db_session)
            user = user_repo.get_by_phone(phone_number)

            if not user:
                return []

            beneficiary_repo = BeneficiaryRepository(db_session)
            beneficiaries = beneficiary_repo.get_all_for_user(str(user.id))

            result = []
            for ben in beneficiaries:
                result.append({
                    "id": str(ben.id),
                    "name": ben.account_name,
                    "nickname": ben.alias or ben.account_name,
                    "account_number": ben.account_number,
                    "bank_name": ben.bank_name,
                    "bank_code": ben.bank_code,
                    "frequency": 0
                })
            return result
    except Exception as e:
        print(f"⚠️  Error loading beneficiaries: {e}")
        return []


@tool
def search_beneficiaries(phone_number: str, search_term: str, db_session: Optional[Session] = None) -> dict[str, Any]:
    """
    Search for saved beneficiaries using fuzzy matching.

    Handles nicknames, typos, and partial matches intelligently.

    Args:
        phone_number: User's phone number
        search_term: Name or nickname to search for (e.g., "mum", "mummy", "mother")
        db_session: Optional existing database session to reuse

    Returns:
        Dictionary with:
        - success: bool
        - matches: list of matching beneficiaries with confidence scores
        - count: number of matches
    """
    try:
        beneficiaries = _get_saved_beneficiaries(phone_number, db_session)
        matches = match_beneficiaries(search_term, beneficiaries)

        return {
            "success": True,
            "matches": matches,
            "count": len(matches)
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Search failed: {str(e)}",
            "matches": [],
            "count": 0
        }


@tool
def calculate_amount(
    expression: str,
    phone_number: str,
    account_id: str = ""
) -> Dict[str, Any]:
    """
    Calculate transfer amount from natural language expressions.

    Supports:
    - Number formats: "5000", "5k", "5,000", "₦5000"
    - Percentages: "50% of balance", "half my balance"
    - Amounts minus fees: "₦5000 less fees"
    - Calculations: "₦10000 minus ₦5000"

    Args:
        expression: Amount expression (e.g., "5k", "50% of balance", "half my balance")
        phone_number: User's phone number
        account_id: Optional specific account to use for balance calculations

    Returns:
        Dictionary with calculated amount and details.
    """

    try:
        expr = expression.lower().strip()

        split_hint = None
        if any(keyword in expr for keyword in ["equal", "even", "each", "between", "among", "share", "split"]):
            split_hint = "equal"

        numeric_match = re.search(
            r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?", expr)
        if numeric_match:
            number = numeric_match.group(1).replace(",", "")
            multiplier_hint = numeric_match.group(2)
            try:
                amount = float(number)
                if multiplier_hint in {"k", "thousand"}:
                    amount *= 1000
                return {
                    "success": True,
                    "amount": amount,
                    "currency": "NGN",
                    "expression": expression,
                    "split_hint": split_hint,
                }
            except ValueError:
                pass

        if "%" in expr or "percent" in expr or "percentage" in expr:
            match = re.search(r"(\d+(?:\.\d+)?)\s*%", expr)
            if match:
                percentage = float(match.group(1))

                if account_id:
                    balance_result = get_account_balance.invoke({
                        "phone_number": phone_number,
                        "account_id": account_id
                    })
                else:
                    balance_result = get_account_balance.invoke(
                        {"phone_number": phone_number})

                if balance_result.get("success"):
                    balance = balance_result.get(
                        "balance") or balance_result.get("total_balance", 0)
                    amount = balance * (percentage / 100)
                    return {
                        "success": True,
                        "amount": round(amount, 2),
                        "percentage": percentage,
                        "base_balance": balance,
                        "currency": "NGN",
                        "expression": expression,
                        "split_hint": split_hint,
                    }

        if "half" in expr:
            percentage = 50
        elif "third" in expr:
            percentage = 33.33
        elif "quarter" in expr:
            percentage = 25
        else:
            percentage = None

        if percentage:
            if account_id:
                balance_result = get_account_balance.invoke({
                    "phone_number": phone_number,
                    "account_id": account_id
                })
            else:
                balance_result = get_account_balance.invoke(
                    {"phone_number": phone_number})

            if balance_result.get("success"):
                balance = balance_result.get(
                    "balance") or balance_result.get("total_balance", 0)
                amount = balance * (percentage / 100)
                return {
                    "success": True,
                    "amount": round(amount, 2),
                    "percentage": percentage,
                    "base_balance": balance,
                    "currency": "NGN",
                    "expression": expression,
                    "split_hint": split_hint,
                }

        if "minus" in expr or "less" in expr:
            parts = re.split(r"(minus|less)", expr)
            if len(parts) >= 3:
                try:
                    amount1 = float(parts[0].replace(
                        "₦", "").replace(",", "").strip())
                    amount2 = float(parts[2].replace("₦", "").replace(
                        ",", "").replace("fees", "50").strip())
                    amount = amount1 - amount2
                    return {
                        "success": True,
                        "amount": max(0, amount),
                        "components": {"amount1": amount1, "amount2": amount2},
                        "currency": "NGN",
                        "expression": expression,
                        "split_hint": split_hint,
                    }
                except (ValueError, IndexError):
                    pass

        return {
            "success": False,
            "error": f"Could not parse amount expression: '{expression}'. Please specify a clear amount like '5000' or '5k'.",
            "amount": None
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Calculation error: {str(e)}",
            "amount": None
        }


@tool
def save_beneficiary(
    phone_number: str,
    name: str,
    account_number: str,
    bank_code: str,
    bank_name: str = "",
    db_session: Optional[Session] = None
) -> Dict[str, Any]:
    """
    Save a new beneficiary for future transfers.

    Args:
        phone_number: User's phone number
        name: Recipient name (e.g., "John Doe", "Mum")
        account_number: 10-digit account number
        bank_code: Bank code (e.g., "058" for GTBank)
        bank_name: Optional bank name (e.g., "GTBank")
        db_session: Optional existing database session to reuse

    Returns:
        Dictionary with saved beneficiary details.
    """
    owns_session = db_session is None

    try:
        if owns_session:
            # Create new UnitOfWork with transaction management
            with UnitOfWork() as uow:
                user = uow.users.get_by_phone(phone_number)

                if not user:
                    return {
                        "success": False,
                        "error": "User not found. Please complete onboarding first."
                    }

                # Check if beneficiary already exists
                from shared.database.models import Beneficiary
                existing = uow.db.query(Beneficiary).filter(
                    Beneficiary.user_id == user.id,
                    Beneficiary.account_number == account_number,
                    Beneficiary.bank_code == bank_code
                ).first()

                if existing:
                    return {
                        "success": True,
                        "beneficiary_id": str(existing.id),
                        "name": existing.account_name,
                        "account_number": existing.account_number,
                        "bank_code": existing.bank_code,
                        "bank_name": existing.bank_name,
                        "message": f"{existing.account_name} is already saved as a beneficiary"
                    }

                beneficiary = uow.beneficiaries.create(
                    user_id=user.id,
                    account_name=name,
                    alias=None,
                    account_number=account_number,
                    bank_code=bank_code,
                    bank_name=bank_name or "Unknown"
                )

                uow.commit()

                return {
                    "success": True,
                    "beneficiary_id": str(beneficiary.id),
                    "name": beneficiary.account_name,
                    "account_number": beneficiary.account_number,
                    "bank_code": beneficiary.bank_code,
                    "bank_name": beneficiary.bank_name,
                    "message": f"{name} has been saved as a beneficiary for future transfers"
                }
        else:
            user_repo = UserRepository(db_session)
            user = user_repo.get_by_phone(phone_number)

            if not user:
                return {
                    "success": False,
                    "error": "User not found. Please complete onboarding first."
                }

            existing = db_session.query(Beneficiary).filter(
                Beneficiary.user_id == user.id,
                Beneficiary.account_number == account_number,
                Beneficiary.bank_code == bank_code
            ).first()

            if existing:
                return {
                    "success": True,
                    "beneficiary_id": str(existing.id),
                    "name": existing.account_name,
                    "account_number": existing.account_number,
                    "bank_code": existing.bank_code,
                    "bank_name": existing.bank_name,
                    "message": f"{existing.account_name} is already saved as a beneficiary"
                }

            beneficiary_repo = BeneficiaryRepository(db_session)
            beneficiary = beneficiary_repo.create(
                user_id=user.id,
                account_name=name,
                alias=None,
                account_number=account_number,
                bank_code=bank_code,
                bank_name=bank_name or "Unknown"
            )

            db_session.flush()

            return {
                "success": True,
                "beneficiary_id": str(beneficiary.id),
                "name": beneficiary.account_name,
                "account_number": beneficiary.account_number,
                "bank_code": beneficiary.bank_code,
                "bank_name": beneficiary.bank_name,
                "message": f"{name} has been saved as a beneficiary for future transfers"
            }
    except Exception as e:
        print(f"⚠️  Error saving beneficiary: {e}")
        return {
            "success": False,
            "error": f"Failed to save beneficiary: {str(e)}"
        }
