"""Transfer patch builders for pending-action edits."""

from typing import Any


def _narration_patch(value: Any) -> dict[str, Any] | None:
    note = str(value or "").strip(" \t\r\n.,;:!?")
    if not note:
        return None
    return {
        "confirmation": {"confirmed": False},
        "authored_narration": note,
        "narration": note,
        "user_note": note,
    }


def _recipient_patch(field: str, value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    patch: dict[str, Any] = {
        "confirmation": {"confirmed": False},
        "resolved_from_saved_beneficiary": False,
        "beneficiary_id": None,
        "beneficiary_candidates": [],
    }
    if field == "recipient_name":
        patch.update(
            {
                "recipient_name": text,
                "recipient_resolved_name": None,
                "recipient_account": None,
                "recipient_account_number": None,
                "recipient_bank_name": None,
                "recipient_bank_code": None,
                "recipient_bank_code_provider": None,
                "recipient_resolution_provider": None,
            }
        )
    elif field == "recipient_account":
        patch["recipient_account"] = text
        patch["recipient_account_number"] = text
        patch["recipient_resolved_name"] = None
        patch["recipient_resolution_provider"] = None
    elif field == "recipient_bank_name":
        patch["recipient_bank_name"] = text
        patch["bank_name"] = text
        patch["recipient_bank_code"] = None
        patch["recipient_bank_code_provider"] = None
        patch["recipient_resolution_provider"] = None
        patch["recipient_resolved_name"] = None
    else:
        return None
    return patch


__all__ = [
    "_narration_patch",
    "_recipient_patch",
]
