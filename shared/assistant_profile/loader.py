"""Loader and cache for assistant profile configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from shared.assistant_profile.models import AssistantProfile
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_PROFILE_CACHE: AssistantProfile | None = None


def _resolve_profile_path(path: str | None) -> str:
    return path or settings.assistant_profile_path


def _load_json_payload(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in assistant profile '{path}': {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Assistant profile '{path}' must contain a top-level JSON object.")
    return payload


def _apply_brand_overrides(value: Any) -> Any:
    if isinstance(value, str):
        return value.format(
            app_name=settings.app_name,
            app_name_short=settings.app_name_short,
            app_creator=settings.app_creator,
        )
    if isinstance(value, list):
        return [_apply_brand_overrides(item) for item in value]
    if isinstance(value, dict):
        return {key: _apply_brand_overrides(item) for key, item in value.items()}
    return value


def load_assistant_profile(path: str | None = None) -> AssistantProfile:
    effective_path = _resolve_profile_path(path)
    payload = cast(dict[str, Any], _apply_brand_overrides(_load_json_payload(effective_path)))
    return cast(AssistantProfile, AssistantProfile.model_validate(payload))


def get_cached_assistant_profile(path: str | None = None, force_reload: bool = False) -> AssistantProfile:
    global _PROFILE_CACHE
    effective_path = _resolve_profile_path(path)

    if _PROFILE_CACHE is not None and not force_reload:
        return _PROFILE_CACHE

    try:
        _PROFILE_CACHE = load_assistant_profile(path=effective_path)
        logger.info("assistant_profile_loaded", path=effective_path, version=_PROFILE_CACHE.version)
    except Exception as exc:
        logger.error("assistant_profile_load_failed", path=effective_path, error=str(exc))
        raise

    return _PROFILE_CACHE
