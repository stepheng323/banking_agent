"""Helpers for interpreting bill-provider execution results."""

from typing import Any

_REFERENCE_KEYS = ("transaction_id", "reference", "ref", "provider_reference")
_STATUS_KEYS = ("status", "provider_status", "transaction_status", "tx_status")
_PROCESSING_STATUSES = {"pending", "processing", "queued"}


def provider_error_message(result: dict[str, Any], fallback: str) -> str:
    for key in ("message", "error", "reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def provider_error_code(result: dict[str, Any]) -> str | None:
    for key in ("response_code", "responseCode", "error_code", "code"):
        value = result.get(key)
        if value is not None:
            return str(value)
    return None


def provider_reference(result: dict[str, Any]) -> str | None:
    for key in _REFERENCE_KEYS:
        value = result.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    for nested_key in ("data", "raw_response"):
        nested = result.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in _REFERENCE_KEYS:
            value = nested.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def provider_status(result: dict[str, Any]) -> str | None:
    for key in _STATUS_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

    for nested_key in ("data", "raw_response"):
        nested = result.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in _STATUS_KEYS:
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()

    for key in ("message", "error", "reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            *_PROCESSING_STATUSES,
            "bill payment is pending",
        }:
            return "pending"
    return None


def provider_status_is_processing(result: dict[str, Any]) -> bool:
    return provider_status(result) in _PROCESSING_STATUSES
