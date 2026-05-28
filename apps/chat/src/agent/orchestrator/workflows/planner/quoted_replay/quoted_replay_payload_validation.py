"""Validation helpers for quoted replay payloads."""

from typing import Any


def _replay_payload_missing_fields(task_type: str, payload: dict[str, Any]) -> list[str]:
    if task_type == "airtime":
        missing: list[str] = []
        if payload.get("amount") is None:
            missing.append("amount")
        if not (payload.get("recipient_phone") or payload.get("target_phone")):
            missing.append("phone number")
        return missing
    if task_type == "data":
        missing = []
        if not (payload.get("target_phone") or payload.get("recipient_phone")):
            missing.append("phone number")
        if not (payload.get("amount") is not None or payload.get("plan_code") or payload.get("plan_name")):
            missing.append("data plan or amount")
        return missing

    missing = []
    if payload.get("amount") is None:
        missing.append("amount")
    if payload.get("beneficiary_id"):
        return missing
    if not (payload.get("recipient_account") or payload.get("recipient_account_number")):
        missing.append("recipient account number")
    if not (payload.get("recipient_bank_code") or payload.get("recipient_bank_name")):
        missing.append("recipient bank")
    return missing


def _is_replay_payload_sufficient(task_type: str, payload: dict[str, Any]) -> bool:
    return not _replay_payload_missing_fields(task_type, payload)


def _format_missing_replay_fields(fields: list[str]) -> str:
    unique_fields = list(dict.fromkeys(field for field in fields if field))
    if not unique_fields:
        return "I can resend that, but I need the missing transaction details first."
    if len(unique_fields) == 1:
        return f"I can resend that, but I need the missing {unique_fields[0]} first."
    field_text = ", ".join(unique_fields[:-1]) + f", and {unique_fields[-1]}"
    return f"I can resend that, but I need the missing {field_text} first."


__all__ = [
    "_format_missing_replay_fields",
    "_is_replay_payload_sufficient",
    "_replay_payload_missing_fields",
]
