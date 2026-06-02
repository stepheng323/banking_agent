"""Canonical account mandate state helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, TypeAlias

from shared.utils.datetime import utc_now_naive

MandateStatus: TypeAlias = Literal["pending", "approved", "ready", "rejected", "cancelled", "paused", "expired"]

PENDING = "pending"
APPROVED = "approved"
READY = "ready"
REJECTED = "rejected"
CANCELLED = "cancelled"
PAUSED = "paused"
EXPIRED = "expired"

AUTHORIZATION_WINDOW = timedelta(hours=1)
CANONICAL_MANDATE_STATUSES: set[str] = {
    PENDING,
    APPROVED,
    READY,
    REJECTED,
    CANCELLED,
    PAUSED,
    EXPIRED,
}
TERMINAL_MANDATE_STATUSES = {REJECTED, CANCELLED, EXPIRED}
PENDING_PROVIDER_STATUSES = {"", "created", "initiated", "pending", "awaiting_authorization"}


def normalize_mandate_status(value: Any) -> str:
    """Normalize provider/application mandate status labels without treating legacy values as ready."""
    status = str(value or "").strip().lower().replace("-", "_")
    if status in PENDING_PROVIDER_STATUSES:
        return PENDING
    if status in {"cancel", "canceled", "cancelled"}:
        return CANCELLED
    if status in {"pause", "paused", "suspended"}:
        return PAUSED
    if status in {"revoke", "revoked"}:
        return CANCELLED
    if status in {"reject", "rejected"}:
        return REJECTED
    if status in {"expire", "expired"}:
        return EXPIRED
    if status in CANONICAL_MANDATE_STATUSES:
        return status
    return status


def account_extra_data(account: Any) -> dict[str, Any]:
    """Return account extra_data as a mutable dict copy."""
    raw = account.get("extra_data") if isinstance(account, dict) else getattr(account, "extra_data", None)
    return dict(raw) if isinstance(raw, dict) else {}


def mandate_authorization_expires_at(account: Any) -> datetime | None:
    """Return the local one-hour NIBSS authorization deadline for an account."""
    extra = account_extra_data(account)
    raw_expiry = extra.get("mandate_authorization_expires_at")
    parsed_expiry = _parse_datetime(raw_expiry)
    if parsed_expiry is not None:
        return parsed_expiry

    created_at = _parse_datetime(extra.get("mandate_created_at"))
    if created_at is None:
        return None
    return created_at + AUTHORIZATION_WINDOW


def is_mandate_authorization_expired(account: Any, *, now: datetime | None = None) -> bool:
    """Return true when a pending mandate has passed Mono's one-hour funding window."""
    status = raw_account_mandate_status(account)
    if status != PENDING:
        return False
    expires_at = mandate_authorization_expires_at(account)
    if expires_at is None:
        return False
    current = now or utc_now_naive()
    return expires_at <= current


def raw_account_mandate_status(account: Any) -> str:
    """Return normalized stored mandate status without applying local expiry."""
    if isinstance(account, str):
        return normalize_mandate_status(account)
    if isinstance(account, dict):
        raw_status = account.get("mandate_status") or account.get("status")
        return normalize_mandate_status(raw_status) if raw_status else ""
    raw_status = getattr(account, "mandate_status", None) or getattr(account, "status", None)
    return normalize_mandate_status(raw_status) if raw_status else ""


def effective_mandate_status(account: Any, *, now: datetime | None = None) -> str:
    """Return the status chat and eligibility should use."""
    status = raw_account_mandate_status(account)
    if status == PENDING and is_mandate_authorization_expired(account, now=now):
        return EXPIRED
    return status


def is_mandate_debit_ready(account: Any, *, now: datetime | None = None) -> bool:
    """Only Mono ready-to-debit mandates may be used for money movement."""
    return effective_mandate_status(account, now=now) == READY


def mandate_authorization_metadata(
    existing_extra: dict[str, Any] | None,
    *,
    created_at: datetime,
    transfer_destinations: list[dict[str, str]],
) -> dict[str, Any]:
    """Build mandate metadata stored on Account.extra_data."""
    return {
        **(existing_extra or {}),
        "mandate_created_at": created_at.isoformat(),
        "mandate_authorization_expires_at": (created_at + AUTHORIZATION_WINDOW).isoformat(),
        "transfer_destinations": list(transfer_destinations),
    }


def is_mandate_transition_allowed(current_status: str, incoming_status: str, *, event_name: str) -> bool:
    """Guard against stale Mono webhooks mutating newer mandate state."""
    current = normalize_mandate_status(current_status)
    incoming = normalize_mandate_status(incoming_status)
    if current in {"active", "successful"}:
        current = APPROVED

    if current == incoming:
        return False
    if current in TERMINAL_MANDATE_STATUSES:
        return False
    if incoming == PENDING:
        return current == PENDING
    if incoming == APPROVED:
        return current == PENDING
    if incoming == READY:
        if current in {PENDING, APPROVED}:
            return True
        return current == PAUSED and event_name == "events.mandate.action.reinstate"
    if incoming == PAUSED:
        return current in {PENDING, APPROVED, READY}
    if incoming in TERMINAL_MANDATE_STATUSES:
        return current in {PENDING, APPROVED, READY, PAUSED}
    return False


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
