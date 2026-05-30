"""Loader and cache for runtime domain guardrails."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from banking.policy.guardrails.models import DomainGuardrails
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_GUARDRAILS_CACHE: DomainGuardrails | None = None


def _resolve_guardrails_path(path: str | None) -> str:
    return path or settings.domain_guardrails_path


def _load_json_payload(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in guardrails file '{path}': {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Guardrails file '{path}' must contain a top-level JSON object.")
    return payload


def load_guardrails(path: str | None = None) -> DomainGuardrails:
    effective_path = _resolve_guardrails_path(path)
    payload = _load_json_payload(effective_path)
    return cast(DomainGuardrails, DomainGuardrails.model_validate(payload))


def get_cached_guardrails(path: str | None = None, force_reload: bool = False) -> DomainGuardrails:
    global _GUARDRAILS_CACHE
    effective_path = _resolve_guardrails_path(path)

    if _GUARDRAILS_CACHE is not None and not force_reload:
        return _GUARDRAILS_CACHE

    try:
        _GUARDRAILS_CACHE = load_guardrails(path=effective_path)
        logger.info("domain_guardrails_loaded", path=effective_path, version=_GUARDRAILS_CACHE.version)
    except Exception as exc:
        logger.error("domain_guardrails_load_failed", path=effective_path, error=str(exc))
        raise

    return _GUARDRAILS_CACHE
