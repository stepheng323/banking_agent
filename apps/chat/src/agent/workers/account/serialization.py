"""Account serialization and selection helpers."""

from typing import Any

from shared.utils.bank_aliases import normalize_bank_name


def serialize_accounts(accounts: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for idx, account in enumerate(accounts, 1):
        if isinstance(account, dict):
            account_id = account.get("account_id") or account.get("id")
            data = {
                "index": idx,
                "account_id": str(account_id or ""),
                "id": str(account_id or ""),
                "bank_name": account.get("bank_name") or account.get("bank") or account.get("name"),
                "account_name": account.get("account_name") or account.get("name_on_account"),
                "account_number": account.get("account_number") or account.get("number"),
                "currency": account.get("currency"),
                "mandate_status": account.get("mandate_status"),
                "available_balance": account.get("available_balance"),
                "balance": account.get("balance"),
                "is_default": account.get("is_default"),
                "extra_data": account.get("extra_data"),
            }
            serialized.append({key: value for key, value in data.items() if value not in (None, "")})
            continue

        data = {
            "index": idx,
            "account_id": str(getattr(account, "account_id", "") or getattr(account, "id", "") or ""),
            "id": str(getattr(account, "account_id", "") or getattr(account, "id", "") or ""),
            "bank_name": getattr(account, "bank_name", None),
            "account_name": getattr(account, "account_name", None),
            "account_number": getattr(account, "account_number", None),
            "currency": getattr(account, "currency", None),
            "mandate_status": getattr(account, "mandate_status", None),
            "available_balance": getattr(account, "available_balance", None),
            "balance": getattr(account, "balance", None),
            "is_default": getattr(account, "is_default", None),
            "extra_data": getattr(account, "extra_data", None),
        }
        serialized.append({key: value for key, value in data.items() if value not in (None, "")})
    return serialized


def find_account_by_bank_name(accounts: list[Any], bank_name: str) -> Any | None:
    bank_name_lower = bank_name.lower().strip()
    normalized_search = normalize_bank_name(bank_name)
    for account in accounts:
        account_bank = account.get("bank_name") if isinstance(account, dict) else getattr(account, "bank_name", None)
        if not account_bank:
            continue
        account_bank_lower = str(account_bank).lower()
        if (
            normalized_search in account_bank_lower
            or account_bank_lower in normalized_search
            or bank_name_lower in account_bank_lower
        ):
            return account
    return None
