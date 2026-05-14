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


def is_capability_supported(
    domain: str,
    action: str,
    policy: CapabilityPolicy | None = None,
) -> bool:
    """Return whether a domain action is currently supported by policy."""
    effective_policy = policy or get_cached_policy()
    domain_policy = effective_policy.capability_matrix.get(domain)
    if domain_policy is None or not domain_policy.enabled:
        return False

    rule = domain_policy.actions.get(action)
    return bool(rule is not None and rule.supported)


def resolve_capability_message(domain: str, action: str, policy: CapabilityPolicy | None = None) -> str | None:
    """Resolve limitation message from policy for a domain action."""
    effective_policy = policy or get_cached_policy()
    domain_policy = effective_policy.capability_matrix.get(domain)
    if domain_policy is not None and not domain_policy.enabled and domain_policy.limitation_message:
        return domain_policy.limitation_message

    rule = resolve_capability_rule(domain=domain, action=action, policy=policy)
    return rule.limitation_message if rule else None


def resolve_capability_alternative(domain: str, action: str, policy: CapabilityPolicy | None = None) -> str | None:
    """Resolve fallback alternative from policy for a domain action."""
    effective_policy = policy or get_cached_policy()
    domain_policy = effective_policy.capability_matrix.get(domain)
    if domain_policy is not None and not domain_policy.enabled:
        return None

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
        if not is_capability_supported(domain=domain, action=action, policy=effective_policy):
            missing.append(action)
    return missing
