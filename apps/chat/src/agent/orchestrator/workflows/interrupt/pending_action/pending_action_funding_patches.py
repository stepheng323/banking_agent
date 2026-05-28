"""Multi-source funding patch builders for pending-action edits."""

from typing import Any


def _pool_funding_base_patch() -> dict[str, Any]:
    return {
        "confirmation": {"confirmed": False},
        "source_account_id": None,
        "source_account_name": None,
        "source_account_number": None,
        "source_bank_name": None,
        "source_account_index": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
    }


def _source_accounts_patch(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    accounts = [str(item).strip() for item in value if str(item or "").strip()]
    if not accounts:
        return None
    return {
        **_pool_funding_base_patch(),
        "source_accounts": accounts,
        "use_dual_accounts": len(accounts) > 1,
        "explicit_split": None,
    }


def _use_dual_accounts_patch(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    enabled = bool(value)
    patch = _pool_funding_base_patch()
    patch["use_dual_accounts"] = enabled
    if not enabled:
        patch["source_accounts"] = None
        patch["explicit_split"] = None
    return patch


def _funding_splits_patch(value: Any) -> dict[str, Any] | None:
    entries = value
    if hasattr(entries, "model_dump"):
        entries = entries.model_dump(exclude_none=True)
    if isinstance(entries, dict):
        split_items = entries.items()
    elif isinstance(entries, list):
        split_items = []
        for item in entries:
            raw_item = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
            if not isinstance(raw_item, dict):
                continue
            split_items.append((raw_item.get("bank_name"), raw_item.get("amount")))
    else:
        return None

    explicit_split: dict[str, float] = {}
    for raw_bank, raw_amount in split_items:
        bank = str(raw_bank or "").strip()
        if not bank:
            continue
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        explicit_split[bank] = amount

    if not explicit_split:
        return None
    return {
        **_pool_funding_base_patch(),
        "source_accounts": list(explicit_split),
        "use_dual_accounts": True,
        "explicit_split": explicit_split,
    }


__all__ = [
    "_funding_splits_patch",
    "_pool_funding_base_patch",
    "_source_accounts_patch",
    "_use_dual_accounts_patch",
]
