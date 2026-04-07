"""Adapters for runtime capability policy behavior."""

from __future__ import annotations

from shared.policy.loader import get_cached_policy
from shared.policy.models import CapabilityPolicy, CapabilityRule


def resolve_capability_rule(
    domain: str,
    action: str,
    policy: CapabilityPolicy | None = None,
) -> CapabilityRule | None:
    """Resolve capability rule for a domain action."""
    effective_policy = policy or get_cached_policy()
    domain_policy = effective_policy.capability_matrix.get(domain)
    if not domain_policy:
        return None
    return domain_policy.actions.get(action)


def resolve_capability_message(domain: str, action: str, policy: CapabilityPolicy | None = None) -> str | None:
    """Resolve limitation message from policy for a domain action."""
    rule = resolve_capability_rule(domain=domain, action=action, policy=policy)
    return rule.limitation_message if rule else None


def resolve_capability_alternative(domain: str, action: str, policy: CapabilityPolicy | None = None) -> str | None:
    """Resolve fallback alternative from policy for a domain action."""
    rule = resolve_capability_rule(domain=domain, action=action, policy=policy)
    return rule.alternative if rule else None


def check_unsupported_actions(
    domain: str,
    requested_actions: list[str],
    policy: CapabilityPolicy | None = None,
) -> list[str]:
    """Return unsupported actions for a domain."""
    effective_policy = policy or get_cached_policy()
    missing: list[str] = []
    for action in requested_actions:
        rule = resolve_capability_rule(domain=domain, action=action, policy=effective_policy)
        if rule is None or not rule.supported:
            missing.append(action)
    return missing
