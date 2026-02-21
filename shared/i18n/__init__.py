"""Deterministic i18n utilities."""

from shared.i18n.bridge import (
    normalize_locale,
    render_capability_limitation,
    render_cancelled_prompt,
    render_generic_capability_blocked,
    render_locale_switched,
    render_policy_notice,
    render_safe_capability_fallback,
)
from shared.i18n.locale import LocaleManager
from shared.i18n.message_keys import MessageKey
from shared.i18n.models import LanguageDetectionSignal, LocaleCode, RenderRequest, RenderResult
from shared.i18n.renderer import render_message, render_text, validate_catalog_completeness

__all__ = [
    "LocaleCode",
    "LanguageDetectionSignal",
    "RenderRequest",
    "RenderResult",
    "MessageKey",
    "LocaleManager",
    "render_message",
    "render_text",
    "validate_catalog_completeness",
    "normalize_locale",
    "render_capability_limitation",
    "render_cancelled_prompt",
    "render_generic_capability_blocked",
    "render_locale_switched",
    "render_policy_notice",
    "render_safe_capability_fallback",
]
