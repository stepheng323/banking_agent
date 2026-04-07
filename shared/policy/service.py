"""Shared runtime capability helpers."""

from __future__ import annotations

from shared.i18n import render_capability_limitation
from shared.policy.adapters import (
    resolve_capability_alternative,
    resolve_capability_message,
    resolve_capability_rule,
)


def capability_block_message(
    domain: str,
    action: str,
    *,
    locale: str = "en",
    action_label: str | None = None,
) -> str | None:
    """Return a user-facing limitation message when an action is unsupported."""
    rule = resolve_capability_rule(domain=domain, action=action)
    if rule is not None and rule.supported:
        return None

    explicit_message = resolve_capability_message(domain=domain, action=action)
    if explicit_message:
        return explicit_message

    alternative = resolve_capability_alternative(domain=domain, action=action)
    resolved_action_label = action_label or action.replace("_", " ")
    alternative_labels = [alternative.replace("_", " ")] if alternative else []
    return render_capability_limitation(
        locale=locale,
        action_label=resolved_action_label,
        alternative_labels=alternative_labels,
    )
