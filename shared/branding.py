"""Brand template helpers backed by runtime settings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape

BRAND_TEMPLATE_KEYS = (
    "app_name",
    "app_name_short",
    "app_initial",
    "app_creator",
    "app_brand_inspiration",
    "app_brand_symbolism",
    "app_public_base_url",
)


def _initial_for(value: str) -> str:
    for char in value.strip():
        if char.strip():
            return char.upper()
    return "A"


def normalize_brand_name(value: str) -> str:
    """Normalize app names/aliases for deterministic matching."""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _normalized_brand_names(values: tuple[str, ...] | list[str] | set[str]) -> set[str]:
    return {normalized for value in values if (normalized := normalize_brand_name(value))}


def brand_name_aliases() -> set[str]:
    """Return normalized current app names and configured aliases."""
    from shared.config.settings import settings

    return _normalized_brand_names((settings.app_name, settings.app_name_short, *settings.app_name_aliases))


def brand_template_params(*, html_escape_values: bool = False) -> dict[str, str]:
    """Return the canonical app/brand template parameters."""
    from shared.config.settings import settings

    params = {
        "app_name": settings.app_name,
        "app_name_short": settings.app_name_short,
        "app_initial": _initial_for(settings.app_name_short or settings.app_name),
        "app_creator": settings.app_creator,
        "app_brand_inspiration": settings.app_brand_inspiration,
        "app_brand_symbolism": settings.app_brand_symbolism,
        "app_public_base_url": settings.app_public_base_url,
    }

    if html_escape_values:
        return {key: escape(value, quote=True) for key, value in params.items()}
    return params


def render_brand_template(
    template: str,
    *,
    extra_params: Mapping[str, object] | None = None,
    html_escape_values: bool = False,
) -> str:
    """Replace known brand placeholders without interpreting unrelated braces."""
    params: dict[str, str] = brand_template_params(html_escape_values=html_escape_values)
    if extra_params:
        params.update({key: str(value) for key, value in extra_params.items()})

    rendered = template
    for key, value in params.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered
