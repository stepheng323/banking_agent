"""
Transfer-specific tools for the LLM-driven transfer agent.
"""
from typing import Dict, Any, List
from datetime import datetime
from langchain.tools import tool

from apps.core.src.agent.banking.tools.account_tools import get_user_accounts, get_account_balance
from apps.core.src.agent.utils.beneficiary_matcher import match_beneficiaries


def _get_saved_beneficiaries(phone_number: str) -> List[Dict[str, Any]]:
    """
    Get saved beneficiaries for a user.

    TODO: Replace with actual database query.
    """
    # Placeholder implementation
    return [
        {
            "id": "ben_001",
            "name": "Mum",
            "nickname": "mummy",
            "account_number": "1234567890",
            "bank_name": "GTBank",
            "bank_code": "058",
            "frequency": 10
        },
        {
            "id": "ben_002",
            "name": "Dad",
            "nickname": "father",
            "account_number": "0987654321",
            "bank_name": "FirstBank",
            "bank_code": "011",
            "frequency": 5
        },
        {
            "id": "ben_003",
            "name": "Sister",
            "nickname": "sissy",
            "account_number": "1111222233",
            "bank_name": "Access Bank",
            "bank_code": "044",
            "frequency": 3
        },
    ]


@tool
def search_beneficiaries(phone_number: str, search_term: str) -> dict[str, Any]:
    """
    Search for saved beneficiaries using fuzzy matching.

    Handles nicknames, typos, and partial matches intelligently.

    Args:
        phone_number: User's phone number
        search_term: Name or nickname to search for (e.g., "mum", "mummy", "mother")

    Returns:
        Dictionary with:
        - success: bool
        - matches: list of matching beneficiaries with confidence scores
        - count: number of matches
    """
    try:
        beneficiaries = _get_saved_beneficiaries(phone_number)
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
    account_id: str = None
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
    import re

    try:
        # Clean expression
        expr = expression.lower().strip()

        # Handle fixed amounts first using regex to capture leading numbers
        split_hint = None
        if any(keyword in expr for keyword in ["equal", "even", "each", "between", "among", "share", "split"]):
            split_hint = "equal"

        numeric_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?", expr)
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

        # Handle percentage of balance
        if "%" in expr or "percent" in expr or "percentage" in expr:
            match = re.search(r"(\d+(?:\.\d+)?)\s*%", expr)
            if match:
                percentage = float(match.group(1))

                # Get balance
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

        # Handle "half", "third", "quarter" etc.
        if "half" in expr:
            percentage = 50
        elif "third" in expr:
            percentage = 33.33
        elif "quarter" in expr:
            percentage = 25
        else:
            percentage = None

        if percentage:
            # Get balance
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

        # Handle subtraction: "₦10000 minus ₦5000"
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
                        "amount": max(0, amount),  # Don't allow negative
                        "components": {"amount1": amount1, "amount2": amount2},
                        "currency": "NGN",
                        "expression": expression,
                        "split_hint": split_hint,
                    }
                except (ValueError, IndexError):
                    pass

        # If we can't parse, return error
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
    bank_name: str = None
) -> Dict[str, Any]:
    """
    Save a new beneficiary for future transfers.

    Args:
        phone_number: User's phone number
        name: Recipient name (e.g., "John Doe", "Mum")
        account_number: 10-digit account number
        bank_code: Bank code (e.g., "058" for GTBank)
        bank_name: Optional bank name (e.g., "GTBank")

    Returns:
        Dictionary with saved beneficiary details.
    """
    try:
        # TODO: Replace with actual database save
        # For now, just return success

        beneficiary_id = f"ben_{int(datetime.now().timestamp())}"

        return {
            "success": True,
            "beneficiary_id": beneficiary_id,
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "bank_name": bank_name or "Unknown",
            "message": f"{name} has been saved as a beneficiary for future transfers"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to save beneficiary: {str(e)}"
        }
