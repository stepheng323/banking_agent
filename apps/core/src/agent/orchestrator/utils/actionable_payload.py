from typing import Any

ACTIONABLE_PAYLOAD_KEYS = (
    "transaction_id",
    "action",
    "amount",
    "beneficiary_id",
    "recipient_name",
    "recipient_resolved_name",
    "recipient_phone",
    "target_phone",
    "recipient_account",
    "recipient_account_number",
    "recipient_bank_code",
    "recipient_bank_name",
    "resolved_from_saved_beneficiary",
    "source_bank_name",
    "narration",
    "network",
    "plan_code",
    "plan_name",
)


def build_actionable_payload(task: Any | None) -> dict[str, Any] | None:
    """Build actionable payload for quote-based follow-up messages."""
    if not task:
        return None

    task_payload = task.payload if isinstance(task.payload, dict) else {}
    idem_key = task_payload.get("idempotency_key")
    tx_id = task_payload.get("transaction_id")
    action = task_payload.get("action")
    if not any([idem_key, tx_id, action]):
        return None

    payload: dict[str, Any] = {
        "idempotency_key": idem_key,
        "task_id": task.id,
        "task_type": task.type,
    }
    for key in ACTIONABLE_PAYLOAD_KEYS:
        value = task_payload.get(key)
        if value is not None and value != "":
            payload[key] = value

    return payload
