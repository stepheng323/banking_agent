"""Loader and cache for Soul policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from shared.config.settings import settings
from shared.policy.models import SoulPolicy
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_POLICY_CACHE: SoulPolicy | None = None


def _resolve_policy_path(path: str | None) -> str:
    """Resolve effective policy path."""
    return path or settings.soul_policy_path


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


def load_policy(path: str | None = None) -> SoulPolicy:
    """Load and validate policy from canonical JSON file."""
    effective_path = _resolve_policy_path(path)
    payload = _load_json_payload(effective_path)
    return cast(SoulPolicy, SoulPolicy.model_validate(payload))


def load_soul_policy(path: str | None = None) -> SoulPolicy:
    """Backward-compatible alias that loads JSON policy only."""
    return load_policy(path=path)


def get_cached_policy(path: str | None = None, force_reload: bool = False) -> SoulPolicy:
    """Get cached policy and fail fast when policy JSON is invalid/missing."""
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
