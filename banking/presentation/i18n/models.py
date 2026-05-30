"""Typed models for deterministic i18n rendering."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class LocaleCode(str, Enum):
    """Phase-1 supported locales."""

    EN = "en"
    PCM = "pcm"
    YO = "yo"
    HA = "ha"
    IG = "ig"


class LanguageDetectionSignal(BaseModel):
    """Signal used to update locale preference."""

    locale: LocaleCode
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "planner"
    explicit: bool = False


class RenderRequest(BaseModel):
    """Request envelope for catalog rendering."""

    message_key: str
    locale: LocaleCode = LocaleCode.EN
    params: dict[str, object] = Field(default_factory=dict)
    fallback_en: str | None = None


class RenderResult(BaseModel):
    """Render result metadata for observability."""

    text: str
    locale: LocaleCode
    from_fallback: bool = False
    missing_key: bool = False
