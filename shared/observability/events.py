"""Portable operational event logging."""

from __future__ import annotations

from typing import Any, Literal

from shared.observability.redaction import redacted_dict
from shared.utils.logging import get_logger

logger = get_logger(__name__)

OperationalSeverity = Literal["info", "warning", "high", "critical"]


def emit_operational_event(
    event: str,
    *,
    severity: OperationalSeverity | str = "info",
    domain: str,
    identifiers: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Emit a vendor-neutral redacted operational event."""
    payload = {
        "event_name": event,
        "severity": severity,
        "domain": domain,
        "identifiers": redacted_dict(identifiers),
        "details": redacted_dict(details),
    }
    log_method = logger.info
    if severity in {"high", "critical"}:
        log_method = logger.error
    elif severity == "warning":
        log_method = logger.warning
    log_method("operational_event", **payload)
