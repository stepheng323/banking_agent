"""Shared runtime capability helpers."""

from __future__ import annotations

from shared.i18n.bridge import render_capability_limitation
from shared.policy.adapters import (
    is_capability_supported,
    resolve_capability_alternative,
    resolve_capability_message,
)
from shared.policy.models import CapabilityPolicy


def capability_block_message(
    domain: str,
    action: str,
    *,
    locale: str = "en",
    action_label: str | None = None,
    policy: CapabilityPolicy | None = None,
) -> str | None:
    """Return a user-facing limitation message when an action is unsupported."""
    if is_capability_supported(domain=domain, action=action, policy=policy):
        return None

    explicit_message = resolve_capability_message(domain=domain, action=action, policy=policy)
    if explicit_message:
        return explicit_message

    alternative = resolve_capability_alternative(domain=domain, action=action, policy=policy)
    resolved_action_label = action_label or action.replace("_", " ")
    alternative_labels = [alternative.replace("_", " ")] if alternative else []
    return render_capability_limitation(
        locale=locale,
        action_label=resolved_action_label,
        alternative_labels=alternative_labels,
    )
