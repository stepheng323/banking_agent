"""Loader and cache for Soul policy."""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.policy.models import SoulPolicy
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_POLICY_CACHE: SoulPolicy | None = None

_JSON_BLOCK_RE = re.compile(
    r"<!--\s*SOUL_POLICY_JSON_START\s*-->\s*```json\s*(\{.*?\})\s*```\s*<!--\s*SOUL_POLICY_JSON_END\s*-->",
    flags=re.DOTALL,
)


def _extract_payload(text: str) -> dict:
    """Extract policy payload from soul.md content."""
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError("Missing SOUL_POLICY_JSON block in soul.md")
    return json.loads(match.group(1))


def load_soul_policy(path: str = "soul.md") -> SoulPolicy:
    """Load and validate Soul policy from file."""
    raw = Path(path).read_text(encoding="utf-8")
    payload = _extract_payload(raw)
    return SoulPolicy.model_validate(payload)


def get_cached_policy(path: str = "soul.md", force_reload: bool = False) -> SoulPolicy:
    """Get cached policy and fail fast when policy is invalid/missing."""
    global _POLICY_CACHE

    if _POLICY_CACHE is not None and not force_reload:
        return _POLICY_CACHE

    try:
        _POLICY_CACHE = load_soul_policy(path=path)
        logger.info("policy_loaded", path=path, version=_POLICY_CACHE.version)
    except Exception as exc:
        logger.error("policy_load_failed", path=path, error=str(exc))
        raise

    return _POLICY_CACHE
