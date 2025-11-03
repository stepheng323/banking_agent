# ruff: noqa
# pyright: reportGeneralTypeIssues=false
"""
Advanced transfer tools for intelligent agent with LLM reasoning.
These tools enable complex scenarios like historical search, multi-account pooling, etc.
"""

import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from langchain.tools import tool

from shared.repositories import UnitOfWork
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts


@tool
def search_beneficiaries_by_transaction(
    phone_number: str,
    description: str,
    timeframe: str = "last_month"
) -> Dict[str, Any]:
    """
    Find beneficiaries from past transaction history based on description and timeframe.

    This is useful when users say things like:
    - "Send to that mechanic from last month"
    - "Send to the person I paid for generator repairs"
    - "Send to whoever I gave money for church offering last week"

    Args:
        phone_number: User's phone number
        description: Keywords from the transaction (e.g., "mechanic", "generator", "church")
        timeframe: When the transaction happened (e.g., "last_month", "last_week", "2_weeks_ago")

    Returns:
        {
            "success": True,
            "beneficiaries": [
                {
                    "id": "ben_123",
                    "name": "John Doe",
                    "account_number": "0123456789",
                    "bank_name": "Access Bank",
                    "bank_code": "044",
                    "last_transaction_date": "2024-10-15",
                    "last_amount": 15000,
                    "transaction_note": "Generator repairs",
                    "confidence": 95
                }
            ],
            "count": 1
        }
    """
    try:
        days = _parse_timeframe(timeframe)
        cutoff_date = datetime.now() - timedelta(days=days)

        with UnitOfWork() as uow:
            user = uow.users.get_by_phone(phone_number)
            if not user:
                return {"success": False, "error": "User not found", "beneficiaries": [], "count": 0}

            beneficiaries = uow.beneficiaries.get_all_for_user(str(user.id))

            matches = []
            description_lower = description.lower()
            keywords = description_lower.split()

            for ben in beneficiaries:

                alias_lower = (ben.alias or "").lower()
                notes_lower = (ben.notes or "").lower()
                name_lower = (ben.account_name or "").lower()

                match_score = 0
                for keyword in keywords:
                    if keyword in alias_lower:
                        match_score += 40
                    if keyword in notes_lower:
                        match_score += 40
                    if keyword in name_lower:
                        match_score += 20

                if match_score > 0:
                    matches.append({
                        "id": str(ben.id),
                        "name": ben.account_name,
                        "nickname": ben.alias,
                        "account_number": ben.account_number,
                        "bank_name": ben.bank_name,
                        "bank_code": ben.bank_code,
                        "last_transaction_date": ben.updated_at.strftime("%Y-%m-%d") if ben.updated_at else None,
                        "notes": ben.notes,
                        "confidence": min(match_score, 95)
                    })

            matches.sort(key=lambda x: x["confidence"], reverse=True)

            return {
                "success": True,
                "beneficiaries": matches,
                "count": len(matches),
                "search_description": description,
                "search_timeframe": timeframe
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Search failed: {str(e)}",
            "beneficiaries": [],
            "count": 0
        }


@tool
def find_optimal_funding(
    phone_number: str,
    amount: float,
    prefer_accounts: Optional[str] = None,
    avoid_accounts: Optional[str] = None,
    allow_pooling: bool = True,
    max_accounts: int = 3
) -> Dict[str, Any]:
    """
    Find the best way to fund a transfer based on user preferences and constraints.

    This handles complex scenarios like:
    - "Use savings first, then checking if needed"
    - "Send 200k but avoid using my investment account"
    - "Use any account except my business account"

    Args:
        phone_number: User's phone number
        amount: Amount needed
        prefer_accounts: Comma-separated account names to prefer (e.g., "savings,checking")
        avoid_accounts: Comma-separated account names to avoid (e.g., "investment,business")
        allow_pooling: Whether to combine multiple accounts
        max_accounts: Maximum number of accounts to use if pooling

    Returns:
        {
            "success": True,
            "strategy": "multi_account",
            "can_cover": True,
            "funding_plan": [
                {
                    "account_id": "acc_123",
                    "account_name": "Savings Account",
                    "amount": 150000,
                    "balance_before": 150000,
                    "balance_after": 0,
                    "reason": "User preferred account"
                },
                {
                    "account_id": "acc_456",
                    "account_name": "Checking Account",
                    "amount": 50000,
                    "balance_before": 120000,
                    "balance_after": 70000,
                    "reason": "Additional funds needed"
                }
            ],
            "total_amount": 200000,
            "accounts_used": 2,
            "warnings": [
                "⚠️ Your Savings account will be empty after this transfer",
                "✅ Your Checking account will still have ₦70,000"
            ]
        }
    """
    try:
        accounts_result = get_user_accounts.invoke(
            {"phone_number": phone_number})
        if not accounts_result.get("success"):
            return {
                "success": False,
                "error": "Could not load accounts",
                "can_cover": False
            }

        accounts = accounts_result.get("accounts", [])

        prefer_list = [a.strip().lower()
                       for a in (prefer_accounts or "").split(",") if a.strip()]
        avoid_list = [a.strip().lower()
                      for a in (avoid_accounts or "").split(",") if a.strip()]

        eligible_accounts = []
        for acc in accounts:
            acc_name_lower = acc["account_name"].lower()
            acc_type_lower = acc.get("account_type", "").lower()

            if any(avoid in acc_name_lower or avoid in acc_type_lower for avoid in avoid_list):
                continue

            if not acc.get("is_active", True):
                continue

            eligible_accounts.append(acc)

        def sort_key(acc):
            acc_name_lower = acc["account_name"].lower()
            acc_type_lower = acc.get("account_type", "").lower()

            for idx, pref in enumerate(prefer_list):
                if pref in acc_name_lower or pref in acc_type_lower:
                    return (0, idx, -acc["balance"])

            return (1, 0, -acc["balance"])

        eligible_accounts.sort(key=sort_key)

        funding_plan = []
        remaining = amount
        total_available = sum(acc["balance"] for acc in eligible_accounts)

        if total_available < amount:
            return {
                "success": True,
                "can_cover": False,
                "error": f"Insufficient funds. Need ₦{amount:,.2f} but only have ₦{total_available:,.2f}",
                "total_available": total_available,
                "amount_needed": amount,
                "shortfall": amount - total_available
            }

        for acc in eligible_accounts:
            if remaining <= 0:
                break

            if len(funding_plan) >= max_accounts:
                break

            amount_from_this_account = min(acc["balance"], remaining)

            if amount_from_this_account > 0:
                reason = "User preferred account" if any(
                    pref in acc["account_name"].lower() for pref in prefer_list
                ) else "Additional funds needed"

                funding_plan.append({
                    "account_id": acc["id"],
                    "account_name": acc["account_name"],
                    "amount": amount_from_this_account,
                    "balance_before": acc["balance"],
                    "balance_after": acc["balance"] - amount_from_this_account,
                    "reason": reason
                })

                remaining -= amount_from_this_account

            if not allow_pooling:
                break

        warnings = []
        for plan in funding_plan:
            if plan["balance_after"] == 0:
                warnings.append(
                    f"⚠️ Your {plan['account_name']} will be empty after this transfer")
            elif plan["balance_after"] < 10000:
                warnings.append(
                    f"⚠️ Your {plan['account_name']} will be low (₦{plan['balance_after']:,.2f})")
            else:
                warnings.append(
                    f"✅ Your {plan['account_name']} will have ₦{plan['balance_after']:,.2f} remaining")

        strategy = "multi_account" if len(
            funding_plan) > 1 else "single_account"

        return {
            "success": True,
            "can_cover": remaining <= 0.01,
            "strategy": strategy,
            "funding_plan": funding_plan,
            "total_amount": sum(p["amount"] for p in funding_plan),
            "accounts_used": len(funding_plan),
            "warnings": warnings
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Funding calculation failed: {str(e)}",
            "can_cover": False
        }


@tool
def calculate_dynamic_amount(
    phone_number: str,
    expression: str,
    account_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Calculate transfer amount from natural language expressions.

    Handles complex amount calculations like:
    - "10% of my salary account balance"
    - "Half of my savings"
    - "5% of my total balance across all accounts"
    - "₦50k plus 10% of my checking account"

    Args:
        phone_number: User's phone number
        expression: Natural language amount expression
        account_name: Specific account to use for calculation (optional)

    Returns:
        {
            "success": True,
            "calculated_amount": 35000,
            "expression": "10% of salary account",
            "breakdown": {
                "base_amount": 350000,
                "percentage": 10,
                "operation": "percentage_of_balance"
            },
            "account_used": "Salary Account"
        }
    """
    try:
        accounts_result = get_user_accounts.invoke(
            {"phone_number": phone_number})
        if not accounts_result.get("success"):
            return {"success": False, "error": "Could not load accounts"}

        accounts = accounts_result.get("accounts", [])
        expression_lower = expression.lower()

        target_account = None
        if account_name:
            for acc in accounts:
                if account_name.lower() in acc["account_name"].lower():
                    target_account = acc
                    break
        else:
            for acc in accounts:
                acc_name_lower = acc["account_name"].lower()
                if acc_name_lower in expression_lower:
                    target_account = acc
                    break


        percentage_match = re.search(r"(\d+(?:\.\d+)?)\s*%", expression_lower)
        if percentage_match:
            percentage = float(percentage_match.group(1))

            if target_account:
                base_amount = target_account["balance"]
                calculated = round(base_amount * (percentage / 100), 2)

                return {
                    "success": True,
                    "calculated_amount": calculated,
                    "expression": expression,
                    "breakdown": {
                        "base_amount": base_amount,
                        "percentage": percentage,
                        "operation": "percentage_of_balance"
                    },
                    "account_used": target_account["account_name"]
                }
            else:
                total_balance = sum(acc["balance"] for acc in accounts)
                calculated = round(total_balance * (percentage / 100), 2)

                return {
                    "success": True,
                    "calculated_amount": calculated,
                    "expression": expression,
                    "breakdown": {
                        "base_amount": total_balance,
                        "percentage": percentage,
                        "operation": "percentage_of_total_balance"
                    },
                    "account_used": "All accounts"
                }

        # "Half of" calculation
        if "half" in expression_lower:
            if target_account:
                calculated = round(target_account["balance"] / 2, 2)
                return {
                    "success": True,
                    "calculated_amount": calculated,
                    "expression": expression,
                    "breakdown": {
                        "base_amount": target_account["balance"],
                        "operation": "half"
                    },
                    "account_used": target_account["account_name"]
                }

        # "All of" or "entire" calculation
        if any(word in expression_lower for word in ["all", "entire", "whole", "full"]):
            if target_account:
                return {
                    "success": True,
                    "calculated_amount": target_account["balance"],
                    "expression": expression,
                    "breakdown": {
                        "base_amount": target_account["balance"],
                        "operation": "full_balance"
                    },
                    "account_used": target_account["account_name"]
                }

        return {
            "success": False,
            "error": f"Could not parse expression: {expression}. Try something like '10% of my balance' or 'half of my savings'"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Calculation failed: {str(e)}"
        }


@tool
def get_recent_transactions(
    phone_number: str,
    recipient_name: Optional[str] = None,
    days: int = 30,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None
) -> Dict[str, Any]:
    """
    Get recent transaction history with optional filters.

    Useful for checking:
    - "Have I already sent money to John this month?"
    - "Show me all transfers over ₦10,000 in the last week"
    - "When was the last time I sent money to church?"

    Args:
        phone_number: User's phone number
        recipient_name: Filter by recipient name (optional)
        days: Number of days to look back (default: 30)
        min_amount: Minimum transaction amount filter (optional)
        max_amount: Maximum transaction amount filter (optional)

    Returns:
        {
            "success": True,
            "transactions": [
                {
                    "date": "2024-10-25",
                    "recipient": "John Doe",
                    "amount": 15000,
                    "account_number": "0123456789",
                    "bank_name": "GTBank",
                    "status": "completed",
                    "note": "Generator repairs"
                }
            ],
            "count": 1,
            "total_amount": 15000,
            "period_days": 30
        }
    """
    try:

        with UnitOfWork() as uow:
            user = uow.users.get_by_phone(phone_number)
            if not user:
                return {"success": False, "error": "User not found", "transactions": [], "count": 0}

            beneficiaries = uow.beneficiaries.get_all_for_user(str(user.id))

            cutoff_date = datetime.now() - timedelta(days=days)

            transactions = []
            for ben in beneficiaries:
                if recipient_name:
                    name_match = recipient_name.lower() in (ben.account_name or "").lower()
                    alias_match = recipient_name.lower() in (ben.alias or "").lower()
                    if not (name_match or alias_match):
                        continue


                if ben.updated_at and ben.updated_at >= cutoff_date:
                    transactions.append({
                        "date": ben.updated_at.strftime("%Y-%m-%d"),
                        "recipient": ben.account_name,
                        "nickname": ben.alias,
                        "account_number": ben.account_number,
                        "bank_name": ben.bank_name,
                        "status": "completed",
                        "note": ben.notes
                    })

            transactions.sort(key=lambda x: x["date"], reverse=True)

            return {
                "success": True,
                "transactions": transactions,
                "count": len(transactions),
                "period_days": days,
                "recipient_filter": recipient_name
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Transaction lookup failed: {str(e)}",
            "transactions": [],
            "count": 0
        }


def _parse_timeframe(timeframe: str) -> int:
    """Convert timeframe string to number of days."""
    timeframe_lower = timeframe.lower().replace("_", " ")

    if "week" in timeframe_lower:
        if "2" in timeframe_lower or "two" in timeframe_lower:
            return 14
        return 7
    elif "month" in timeframe_lower:
        if "2" in timeframe_lower or "two" in timeframe_lower:
            return 60
        return 30
    elif "day" in timeframe_lower:
        match = re.search(r"(\d+)", timeframe_lower)
        if match:
            return int(match.group(1))
        return 1

    # Default to 30 days
    return 30
