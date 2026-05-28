"""Tests for deterministic i18n locale and rendering behavior."""

from __future__ import annotations

import pytest

from shared.i18n.locale import LocaleManager
from shared.i18n.message_keys import ALL_MESSAGE_KEYS
from shared.i18n.models import (
    LanguageDetectionSignal,
    LocaleCode,
)
from shared.i18n.renderer import (
    _flatten_string_leaves,
    _read_catalog,
    render_message,
    render_text,
    validate_catalog_completeness,
)


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        del ex
        self._store[key] = value

    async def delete(self, key: str):
        self._store.pop(key, None)


def test_locale_normalize_aliases():
    assert LocaleManager.normalize("english") == LocaleCode.EN
    assert LocaleManager.normalize("pidgin") == LocaleCode.PCM
    assert LocaleManager.normalize("yoruba") == LocaleCode.YO
    assert LocaleManager.normalize("hausa") == LocaleCode.HA
    assert LocaleManager.normalize("igbo") == LocaleCode.IG


def test_parse_locale_name():
    assert LocaleManager.parse_locale_name("english") == LocaleCode.EN
    assert LocaleManager.parse_locale_name("English") == LocaleCode.EN
    assert LocaleManager.parse_locale_name("pidgin") == LocaleCode.PCM
    assert LocaleManager.parse_locale_name("yoruba") == LocaleCode.YO
    assert LocaleManager.parse_locale_name("hausa") == LocaleCode.HA
    assert LocaleManager.parse_locale_name("igbo") == LocaleCode.IG
    assert LocaleManager.parse_locale_name("  Yoruba  ") == LocaleCode.YO
    assert LocaleManager.parse_locale_name(None) is None
    assert LocaleManager.parse_locale_name("french") is None


def test_render_message_and_bridge():
    text = render_message("common.safe_capability_fallback", "pcm")
    assert "I never fit do" in text

    en = render_message("common.safe_capability_fallback", "en")
    assert render_text(en, "pcm") == text


@pytest.mark.asyncio
async def test_locale_hysteresis_auto_switch(monkeypatch):
    fake_redis = _FakeRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    phone = "2348000000000"

    first = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.EN, confidence=0.95, source="planner"),
    )
    assert first == LocaleCode.EN

    second = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.YO, confidence=0.95, source="planner"),
    )
    assert second == LocaleCode.EN

    third = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.YO, confidence=0.95, source="planner"),
    )
    assert third == LocaleCode.YO


@pytest.mark.asyncio
async def test_explicit_locale_switch_overrides(monkeypatch):
    fake_redis = _FakeRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    phone = "2348111111111"
    resolved = await LocaleManager.set_locale(phone, "pcm", source="user_command")
    assert resolved == LocaleCode.PCM

    effective = await LocaleManager.get_effective_locale(phone)
    assert effective == LocaleCode.PCM


@pytest.mark.asyncio
async def test_explicit_locale_lock_blocks_auto_detection_override(monkeypatch):
    fake_redis = _FakeRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    phone = "2348222222222"
    resolved = await LocaleManager.set_locale(phone, "pcm", source="user_command")
    assert resolved == LocaleCode.PCM

    updated = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.EN, confidence=0.99, source="planner"),
    )

    assert updated == LocaleCode.PCM
    assert fake_redis._store[f"user:{phone}:language"] == "pcm"
    assert fake_redis._store[f"user:{phone}:language_explicit"] == "1"


def test_catalog_completeness():
    validate_catalog_completeness()


def test_message_key_typing_in_sync():
    en_keys = set(_flatten_string_leaves(_read_catalog(LocaleCode.EN)).keys())
    assert set(ALL_MESSAGE_KEYS) == en_keys


@pytest.mark.parametrize(
    ("locale", "expected_snippet"),
    [
        ("pcm", "You wan save"),
        ("yo", "Ṣe o fẹ́"),
        ("ha", "Kana so"),
        ("ig", "Ị chọrọ"),
    ],
)
def test_beneficiary_suggestion_prompts_are_localized(locale: str, expected_snippet: str):
    text = render_message(
        "beneficiary.suggestion.ask_save_phone_default",
        locale,
        {
            "recipient_display": "Mum",
            "network": "MTN",
            "masked_phone": "…4567",
        },
    )

    assert expected_snippet in text
    assert "Mum" in text
    assert "MTN" in text
    assert "…4567" in text
    assert "Would you like" not in text
