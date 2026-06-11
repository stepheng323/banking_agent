"""Recipient resolution mode helpers for transfer funding paths."""

from __future__ import annotations

from typing import Any, Literal

from banking.transfers.resolution.names import normalize_name

RecipientResolutionMode = Literal["single_source", "pooled"]

SINGLE_SOURCE_MODE: RecipientResolutionMode = "single_source"
POOLED_MODE: RecipientResolutionMode = "pooled"


def is_pooled_funding_plan(plan: Any) -> bool:
    return isinstance(plan, dict) and plan.get("is_single_source") is False


def desired_recipient_resolution_mode(payload: Any) -> RecipientResolutionMode:
    """Return the resolver mode required by the current accepted source state."""
    funding_plan = getattr(payload, "funding_plan", None)
    if is_pooled_funding_plan(funding_plan):
        return POOLED_MODE

    source_accounts = getattr(payload, "source_accounts", None)
    if isinstance(source_accounts, list) and len([item for item in source_accounts if str(item).strip()]) > 1:
        return POOLED_MODE

    explicit_split = getattr(payload, "explicit_split", None)
    if isinstance(explicit_split, dict) and len([key for key in explicit_split if str(key).strip()]) > 1:
        return POOLED_MODE

    if getattr(payload, "use_dual_accounts", None) is True:
        return POOLED_MODE

    return SINGLE_SOURCE_MODE


def provider_identity_is_authoritative(provider: Any) -> bool:
    """Return whether provider account names should be used as user-facing identity."""
    return not bool(getattr(provider, "use_sandbox", False))


def recipient_names_equivalent(left: Any, right: Any) -> bool:
    normalized_left = normalize_name(str(left or ""))
    normalized_right = normalize_name(str(right or ""))
    return bool(normalized_left and normalized_right and normalized_left == normalized_right)
