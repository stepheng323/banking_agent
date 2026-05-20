"""Status normalization helpers for support handlers."""

from __future__ import annotations

from typing import Any


def normalize_transaction_status(status: Any) -> str:
    """Map provider/database status variants into support-facing states."""
    raw = getattr(status, "value", status)
    normalized = str(raw or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]

    if normalized in {"success", "successful", "complete", "completed", "confirmed"}:
        return "successful"
    if normalized in {"pending", "processing", "queued", "initiated", "in_progress"}:
        return "processing" if normalized != "pending" else "pending"
    if normalized in {
        "fail",
        "failed",
        "failure",
        "error",
        "errored",
        "declined",
        "rejected",
        "failed_transfer",
        "transfer_failed",
    }:
        return "failed"
    if normalized in {"reversed", "refunded"}:
        return "reversed"
    return normalized or "unknown"


def resolve_transaction_status(transaction: dict[str, Any]) -> str:
    """Resolve status from transaction fields and provider payload fallbacks."""
    for key in ("display_status", "status", "final_status", "provider_status", "transaction_status", "tx_status"):
        status = normalize_transaction_status(transaction.get(key))
        if status != "unknown":
            return status

    provider_response = transaction.get("provider_response", {})
    if isinstance(provider_response, dict):
        for key in ("status", "provider_status", "transaction_status", "tx_status"):
            status = normalize_transaction_status(provider_response.get(key))
            if status != "unknown":
                return status

    return "unknown"
