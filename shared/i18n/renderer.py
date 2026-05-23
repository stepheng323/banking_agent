"""Deterministic i18n renderer backed by static JSON catalogs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from shared.i18n.message_keys import MessageKey
from shared.i18n.models import LocaleCode
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_LOCALES: tuple[LocaleCode, ...] = (
    LocaleCode.EN,
    LocaleCode.PCM,
    LocaleCode.YO,
    LocaleCode.HA,
    LocaleCode.IG,
)

_CATALOG_CACHE: dict[LocaleCode, dict[str, Any]] = {}
_KEY_BY_EN_TEXT: dict[str, MessageKey] | None = None


def _catalog_path(locale: LocaleCode) -> Path:
    return Path(__file__).resolve().parent / "catalog" / f"{locale.value}.json"


def _read_catalog(locale: LocaleCode) -> dict[str, Any]:
    if locale in _CATALOG_CACHE:
        return _CATALOG_CACHE[locale]

    path = _catalog_path(locale)
    if not path.exists():
        logger.warning("i18n_catalog_missing", locale=locale.value, path=str(path))
        _CATALOG_CACHE[locale] = {}
        return _CATALOG_CACHE[locale]

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = cast(dict[str, Any], loaded)
        else:
            logger.warning("i18n_catalog_invalid_shape", locale=locale.value, path=str(path))
            data = {}
    except Exception as exc:
        logger.error("i18n_catalog_load_failed", locale=locale.value, error=str(exc), path=str(path))
        data = {}

    _CATALOG_CACHE[locale] = data
    return data


def _get_by_dotted_key(payload: dict[str, Any], dotted_key: str) -> str | None:
    node: Any = payload
    for segment in dotted_key.split("."):
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]

    return node if isinstance(node, str) else None


def _flatten_string_leaves(payload: dict[str, Any], prefix: str = "") -> dict[str, str]:
    leaves: dict[str, str] = {}
    for key, value in payload.items():
        composed = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            leaves[composed] = value
        elif isinstance(value, dict):
            leaves.update(_flatten_string_leaves(value, composed))
    return leaves


def _render_template(template: str, params: dict[str, object] | None) -> str:
    if not params:
        return template
    try:
        return template.format(**params)
    except Exception as exc:
        logger.warning("i18n_template_params_failed", error=str(exc), template=template)
        return template


def _default_template_params() -> dict[str, object]:
    from shared.branding import brand_template_params

    return brand_template_params()


def render_message(
    message_key: MessageKey,
    locale: str | LocaleCode,
    params: dict[str, object] | None = None,
    fallback_en: str | None = None,
) -> str:
    """Render a keyed message in a locale with English fallback."""
    from shared.i18n.locale import LocaleManager

    resolved_locale = LocaleManager.normalize(locale)

    merged_params = _default_template_params()
    if params:
        merged_params.update(params)

    locale_catalog = _read_catalog(resolved_locale)
    template = _get_by_dotted_key(locale_catalog, message_key)

    if template is not None:
        return _render_template(template, merged_params)

    logger.warning("i18n_key_missing", locale=resolved_locale.value, key=message_key)

    en_catalog = _read_catalog(LocaleCode.EN)
    en_template = _get_by_dotted_key(en_catalog, message_key)
    if en_template is not None:
        logger.info("i18n_fallback_en_used", locale=resolved_locale.value, key=message_key)
        return _render_template(en_template, merged_params)

    if fallback_en is not None:
        logger.info("i18n_fallback_en_used", locale=resolved_locale.value, key=message_key, source="inline")
        return _render_template(fallback_en, merged_params)

    return message_key


def _en_text_to_key() -> dict[str, MessageKey]:
    global _KEY_BY_EN_TEXT
    if _KEY_BY_EN_TEXT is None:
        leaves = _flatten_string_leaves(_read_catalog(LocaleCode.EN))
        _KEY_BY_EN_TEXT = {value: cast(MessageKey, key) for key, value in leaves.items()}
    return _KEY_BY_EN_TEXT


def render_text(raw_en_text: str, locale: str | LocaleCode) -> str:
    """Transitional deterministic rendering for raw English strings."""
    from shared.i18n.locale import LocaleManager

    resolved_locale = LocaleManager.normalize(locale)
    if resolved_locale == LocaleCode.EN:
        return raw_en_text

    en_map = _en_text_to_key()
    key = en_map.get(raw_en_text)

    if not key:
        logger.info("i18n_bridge_used", mode="passthrough", locale=resolved_locale.value)
        return raw_en_text

    logger.info("i18n_bridge_used", mode="catalog_match", locale=resolved_locale.value, key=key)
    return render_message(key, resolved_locale)


def validate_catalog_completeness(required_locales: tuple[LocaleCode, ...] = SUPPORTED_LOCALES) -> None:
    """Fail fast when non-English catalogs miss required keys."""
    en_keys = set(_flatten_string_leaves(_read_catalog(LocaleCode.EN)).keys())
    errors: list[str] = []

    if not en_keys:
        raise ValueError("i18n catalog validation failed: en catalog is empty")

    for locale in required_locales:
        payload = _read_catalog(locale)
        locale_keys = set(_flatten_string_leaves(payload).keys())
        missing = sorted(en_keys - locale_keys)
        if missing:
            sample = ", ".join(missing[:8])
            errors.append(f"{locale.value}: missing {len(missing)} keys ({sample})")

    if errors:
        raise ValueError("i18n catalog validation failed: " + " | ".join(errors))
