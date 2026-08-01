"""Shared runtime capability helpers."""

from __future__ import annotations

from typing import cast

from banking.policy.adapters import (
    is_capability_supported,
    resolve_capability_alternative,
    resolve_capability_message,
)
from banking.policy.models import AvailableConversationalSuggestion, CapabilityPolicy
from banking.presentation.i18n.bridge import render_capability_limitation


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


def resolve_available_conversational_suggestions(
    *,
    locale: str = "en",
    policy: CapabilityPolicy | None = None,
) -> list[AvailableConversationalSuggestion]:
    """Return a localized list of currently supported capability suggestions."""
    from banking.policy.loader import get_cached_policy
    from banking.presentation.i18n.message_keys import MessageKey
    from banking.presentation.i18n.renderer import message_key_exists, render_message

    effective_policy = policy or get_cached_policy()
    available: list[AvailableConversationalSuggestion] = []

    for suggestion in effective_policy.conversational_suggestions:
        if is_capability_supported(domain=suggestion.domain, action=suggestion.action, policy=effective_policy):
            if not message_key_exists(suggestion.label_key, locale):
                continue
            label = render_message(cast(MessageKey, suggestion.label_key), locale)
            if label:
                available.append(AvailableConversationalSuggestion(id=suggestion.id, label=label))

    return available
