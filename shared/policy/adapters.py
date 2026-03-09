"""Adapters for policy-driven prompt and capability behavior."""

from __future__ import annotations

from typing import Any

from shared.policy.loader import get_cached_policy
from shared.policy.models import CapabilityRule, SoulPolicy


def build_system_profile(policy: SoulPolicy) -> dict[str, Any]:
    """Build SystemProfile-compatible payload from policy."""
    return {
        "name": policy.identity.name,
        "description": policy.identity.description,
        "positioning": policy.identity.positioning,
        "creator": policy.identity.creator,
        "brand_origin": policy.identity.brand_origin,
        "supported_domains": policy.supported_domains,
        "unsupported_capabilities": policy.unsupported_capabilities,
        "tone": policy.tone.style,
    }


def build_planner_policy_block(policy: SoulPolicy) -> str:
    """Build compact planner grounding block from policy."""
    supported = ", ".join(policy.supported_domains) or "None"
    unsupported = ", ".join(policy.unsupported_capabilities) or "None"
    tone_rules = "; ".join(policy.tone.response_rules) if policy.tone.response_rules else policy.tone.style
    safety = "; ".join(policy.safety_rules) if policy.safety_rules else "Banking only."

    return (
        "## SOUL POLICY (Authoritative Guardrails)\n"
        f"- Identity: {policy.identity.name}. {policy.identity.description}\n"
        f"- Positioning: {policy.identity.positioning}\n"
        f"- Tone: {policy.tone.style} ({policy.tone.brevity})\n"
        f"- Supported domains: {supported}\n"
        f"- Unsupported capabilities: {unsupported}\n"
        f"- Response rules: {tone_rules}\n"
        f"- Safety rules: {safety}\n"
        "- For unsupported requests, classify safely and redirect to supported banking tasks."
    )


def build_planner_policy_summary(policy: SoulPolicy) -> str:
    """Build planner-only policy summary focused on extraction behavior."""
    def _preview(items: list[str]) -> str:
        if not items:
            return "None"
        first = items[0]
        remaining = len(items) - 1
        return f"{first} (+{remaining})" if remaining > 0 else first

    def _clip(text: str, *, limit: int = 56) -> str:
        compact = " ".join(text.strip().split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"

    tone_rule = (
        policy.tone.response_rules[0]
        if policy.tone.response_rules
        else policy.tone.style
    )
    safety_rule = (
        policy.safety_rules[0]
        if policy.safety_rules
        else "Prioritize banking/support workflows only"
    )

    return (
        "## POLICY\n"
        f"- Supported(policy): {_preview(policy.supported_domains).replace(' (+', '(+')}; "
        f"Unsupported(policy): {_preview(policy.unsupported_capabilities).replace(' (+', '(+')}.\n"
        f"- Tone/Safety: {_clip(tone_rule, limit=24)} | {_clip(safety_rule, limit=24)}.\n"
        "- OOS -> conversational.out_of_scope.\n"
        "- Reply in user language."
    )


def resolve_capability_rule(domain: str, action: str, policy: SoulPolicy | None = None) -> CapabilityRule | None:
    """Resolve capability rule for a domain action."""
    effective_policy = policy or get_cached_policy()
    domain_policy = effective_policy.capability_matrix.get(domain)
    if not domain_policy:
        return None
    return domain_policy.actions.get(action)


def resolve_capability_message(domain: str, action: str, policy: SoulPolicy | None = None) -> str | None:
    """Resolve limitation message from policy for a domain action."""
    rule = resolve_capability_rule(domain=domain, action=action, policy=policy)
    return rule.limitation_message if rule else None


def resolve_capability_alternative(domain: str, action: str, policy: SoulPolicy | None = None) -> str | None:
    """Resolve fallback alternative from policy for a domain action."""
    rule = resolve_capability_rule(domain=domain, action=action, policy=policy)
    return rule.alternative if rule else None


def build_meta_policy_payload(policy: SoulPolicy) -> dict[str, Any]:
    """Build payload for meta reply grounding."""
    return build_system_profile(policy) | {
        "response_rules": policy.tone.response_rules,
        "safety_rules": policy.safety_rules,
    }
