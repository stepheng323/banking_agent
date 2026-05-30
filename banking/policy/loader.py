"""Loader and cache for runtime capability policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from banking.policy.models import CapabilityPolicy
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_POLICY_CACHE: CapabilityPolicy | None = None


def _resolve_policy_path(path: str | None) -> str:
    """Resolve effective capability policy path."""
    return path or settings.capability_policy_path


def _load_json_payload(path: str) -> dict[str, Any]:
    """Load and validate that policy payload is a JSON object."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in policy file '{path}': {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Policy file '{path}' must contain a top-level JSON object.")
    return payload


def load_policy(path: str | None = None) -> CapabilityPolicy:
    """Load and validate capability policy from canonical JSON file."""
    effective_path = _resolve_policy_path(path)
    payload = _load_json_payload(effective_path)
    return cast(CapabilityPolicy, CapabilityPolicy.model_validate(payload))


def get_cached_policy(path: str | None = None, force_reload: bool = False) -> CapabilityPolicy:
    """Get cached capability policy and fail fast when JSON is invalid/missing."""
    global _POLICY_CACHE
    effective_path = _resolve_policy_path(path)

    if _POLICY_CACHE is not None and not force_reload:
        return _POLICY_CACHE

    try:
        _POLICY_CACHE = load_policy(path=effective_path)
        logger.info("policy_loaded", path=effective_path, version=_POLICY_CACHE.version)
    except Exception as exc:
        logger.error("policy_load_failed", path=effective_path, error=str(exc))
        raise

    return _POLICY_CACHE
