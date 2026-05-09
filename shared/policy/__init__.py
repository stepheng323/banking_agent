"""Runtime capability policy package."""

from shared.policy.adapters import (
    check_unsupported_actions,
    resolve_capability_alternative,
    resolve_capability_message,
    resolve_capability_rule,
)
from shared.policy.loader import get_cached_policy, load_policy
from shared.policy.models import (
    CapabilityPolicy,
    CapabilityRule,
    DomainCapabilityPolicy,
)
from shared.policy.service import capability_block_message
from shared.policy.validation import validate_policy_coverage

__all__ = [
    "CapabilityPolicy",
    "CapabilityRule",
    "DomainCapabilityPolicy",
    "capability_block_message",
    "check_unsupported_actions",
    "load_policy",
    "get_cached_policy",
    "resolve_capability_rule",
    "resolve_capability_message",
    "resolve_capability_alternative",
    "validate_policy_coverage",
]
