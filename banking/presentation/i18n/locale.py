"""Locale management and language signal handling."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import ClassVar

from banking.presentation.i18n.models import LanguageDetectionSignal, LocaleCode
from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class LocaleManager:
    """Persisted locale manager with hysteresis for auto-switching."""

    DEFAULT_LOCALE: ClassVar[LocaleCode] = LocaleCode.EN
    AUTO_SWITCH_CONFIDENCE: ClassVar[float] = 0.8
    AUTO_SWITCH_THRESHOLD: ClassVar[int] = 2
    LOCALE_TTL_SECONDS: ClassVar[int] = 60 * 60 * 24 * 30
    CANDIDATE_TTL_SECONDS: ClassVar[int] = 60 * 60 * 24 * 7
    FUZZY_ALIAS_MIN_LENGTH: ClassVar[int] = 5
    FUZZY_ALIAS_THRESHOLD: ClassVar[float] = 0.82

    _ALIASES: ClassVar[dict[str, LocaleCode]] = {
        "en": LocaleCode.EN,
        "english": LocaleCode.EN,
        "pcm": LocaleCode.PCM,
        "pidgin": LocaleCode.PCM,
        "nigerian pidgin": LocaleCode.PCM,
        "naija": LocaleCode.PCM,
        "yo": LocaleCode.YO,
        "yoruba": LocaleCode.YO,
        "ha": LocaleCode.HA,
        "hausa": LocaleCode.HA,
        "ig": LocaleCode.IG,
        "igbo": LocaleCode.IG,
        "ibo": LocaleCode.IG,
    }

    @classmethod
    def normalize(cls, value: str | LocaleCode | None) -> LocaleCode:
        parsed = cls.parse_locale_name(value)
        return parsed if parsed is not None else cls.DEFAULT_LOCALE

    @classmethod
    def _normalize_alias_token(cls, value: str) -> str:
        token = re.sub(r"[^a-z0-9\s-]+", " ", value.strip().lower())
        return re.sub(r"\s+", " ", token).strip()

    @classmethod
    def _parse_fuzzy_alias(cls, token: str) -> LocaleCode | None:
        if len(token) < cls.FUZZY_ALIAS_MIN_LENGTH or " " in token:
            return None

        best_alias = None
        best_score = 0.0
        for alias in cls._ALIASES:
            if len(alias) < cls.FUZZY_ALIAS_MIN_LENGTH or " " in alias:
                continue
            score = SequenceMatcher(a=token, b=alias).ratio()
            if score > best_score:
                best_alias = alias
                best_score = score

        if best_alias is None or best_score < cls.FUZZY_ALIAS_THRESHOLD:
            return None
        return cls._ALIASES[best_alias]

    @classmethod
    def parse_locale_name(cls, value: str | LocaleCode | None, *, allow_fuzzy: bool = False) -> LocaleCode | None:
        if isinstance(value, LocaleCode):
            return value
        if not value:
            return None

        token = cls._normalize_alias_token(value)
        parsed = cls._ALIASES.get(token)
        if parsed is not None or not allow_fuzzy:
            return parsed
        return cls._parse_fuzzy_alias(token)

    @classmethod
    def aliases_for(cls, locale: str | LocaleCode) -> tuple[str, ...]:
        resolved = cls.parse_locale_name(locale)
        if resolved is None:
            return ()
        return tuple(alias for alias, alias_locale in cls._ALIASES.items() if alias_locale == resolved)

    @classmethod
    def from_detection(cls, detected_language: str | None) -> LocaleCode:
        return cls.normalize(detected_language)

    @classmethod
    def _locale_key(cls, phone_number: str) -> str:
        return f"user:{phone_number}:language"

    @classmethod
    def _candidate_key(cls, phone_number: str) -> str:
        return f"user:{phone_number}:language_candidate"

    @classmethod
    def _explicit_key(cls, phone_number: str) -> str:
        return f"user:{phone_number}:language_explicit"

    @classmethod
    async def get_locale(cls, phone_number: str) -> LocaleCode | None:
        try:
            redis_client = RedisClient.get_client()
            value = await redis_client.get(cls._locale_key(phone_number))
            if not value:
                return None
            return cls.normalize(str(value))
        except Exception as exc:
            logger.warning("locale_read_failed", phone_hash=log_fingerprint(phone_number), error=str(exc))
            return None

    @classmethod
    async def is_explicit_locale(cls, phone_number: str) -> bool:
        try:
            redis_client = RedisClient.get_client()
            value = await redis_client.get(cls._explicit_key(phone_number))
            return str(value).strip().lower() == "1"
        except Exception as exc:
            logger.warning("locale_explicit_read_failed", phone_hash=log_fingerprint(phone_number), error=str(exc))
            return False

    @classmethod
    async def _write_locale(
        cls,
        phone_number: str,
        locale: str | LocaleCode,
        *,
        source: str,
        explicit_override: bool,
    ) -> LocaleCode:
        resolved = cls.normalize(locale)
        try:
            redis_client = RedisClient.get_client()
            await redis_client.set(cls._locale_key(phone_number), resolved.value, ex=cls.LOCALE_TTL_SECONDS)
            if explicit_override:
                await redis_client.set(cls._explicit_key(phone_number), "1", ex=cls.LOCALE_TTL_SECONDS)
            else:
                await redis_client.delete(cls._explicit_key(phone_number))
            await redis_client.delete(cls._candidate_key(phone_number))
            logger.info(
                "locale_switched_explicit" if explicit_override else "locale_updated_detected",
                phone_hash=log_fingerprint(phone_number),
                locale=resolved.value,
                source=source,
            )
        except Exception as exc:
            logger.warning(
                "locale_write_failed",
                phone_hash=log_fingerprint(phone_number),
                error=str(exc),
                locale=resolved.value,
                explicit_override=explicit_override,
            )
        return resolved

    @classmethod
    async def set_locale(cls, phone_number: str, locale: str | LocaleCode, *, source: str = "explicit") -> LocaleCode:
        return await cls._write_locale(phone_number, locale, source=source, explicit_override=True)

    @classmethod
    async def _load_candidate(cls, phone_number: str) -> tuple[LocaleCode | None, int]:
        try:
            redis_client = RedisClient.get_client()
            raw = await redis_client.get(cls._candidate_key(phone_number))
            if not raw:
                return None, 0
            payload = json.loads(raw)
            return cls.normalize(payload.get("locale")), int(payload.get("count", 0))
        except Exception:
            return None, 0

    @classmethod
    async def _save_candidate(cls, phone_number: str, locale: LocaleCode, count: int) -> None:
        try:
            redis_client = RedisClient.get_client()
            payload = json.dumps({"locale": locale.value, "count": count})
            await redis_client.set(cls._candidate_key(phone_number), payload, ex=cls.CANDIDATE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("locale_candidate_write_failed", phone_hash=log_fingerprint(phone_number), error=str(exc))

    @classmethod
    async def update_locale(cls, phone_number: str, signal: LanguageDetectionSignal) -> LocaleCode:
        """Update locale using explicit signal or hysteresis auto-switch."""
        current = await cls.get_locale(phone_number)

        if signal.explicit:
            return await cls.set_locale(phone_number, signal.locale, source=signal.source)

        explicit_locked = await cls.is_explicit_locale(phone_number)
        if explicit_locked and current is not None:
            return current

        candidate = signal.locale
        if current is None:
            return await cls._write_locale(
                phone_number,
                candidate,
                source=f"{signal.source}:initial",
                explicit_override=False,
            )

        if candidate == current:
            try:
                redis_client = RedisClient.get_client()
                await redis_client.delete(cls._candidate_key(phone_number))
            except Exception:
                pass
            return current

        if signal.confidence < cls.AUTO_SWITCH_CONFIDENCE:
            return current

        pending_locale, pending_count = await cls._load_candidate(phone_number)
        new_count = pending_count + 1 if pending_locale == candidate else 1

        await cls._save_candidate(phone_number, candidate, new_count)

        if new_count >= cls.AUTO_SWITCH_THRESHOLD:
            resolved = await cls._write_locale(
                phone_number,
                candidate,
                source=f"{signal.source}:auto",
                explicit_override=False,
            )
            logger.info(
                "locale_switched_auto",
                phone_hash=log_fingerprint(phone_number),
                locale=resolved.value,
                previous=current.value,
            )
            return resolved

        return current

    @classmethod
    async def resolve_turn_locale(
        cls,
        *,
        phone_number: str,
        current_locale: str | LocaleCode | None,
        detected_language: str | None,
        confidence: float,
        source: str,
        persist_detection: bool,
    ) -> LocaleCode:
        """Resolve locale for this response and optionally persist a detection signal.

        The turn should answer in a confidently detected supported language even
        before the persisted preference crosses the auto-switch hysteresis
        threshold.  An explicit preference always wins.  This keeps response
        language coherent while still avoiding preference flapping.
        """
        current = cls.normalize(current_locale)
        detected = cls.parse_locale_name(detected_language)
        if detected is None:
            return current

        if not persist_detection:
            return detected

        if await cls.is_explicit_locale(phone_number):
            return current

        await cls.update_locale(
            phone_number,
            LanguageDetectionSignal(
                locale=detected,
                confidence=confidence,
                source=source,
                explicit=False,
            ),
        )
        return detected

    @classmethod
    async def get_effective_locale(
        cls,
        phone_number: str,
        detected_language: str | None = None,
    ) -> LocaleCode:
        stored = await cls.get_locale(phone_number)
        if stored is not None:
            logger.info(
                "locale_resolved",
                phone_hash=log_fingerprint(phone_number),
                locale=stored.value,
                source="stored",
            )
            return stored

        if detected_language:
            detected = cls.from_detection(detected_language)
            logger.info(
                "locale_resolved",
                phone_hash=log_fingerprint(phone_number),
                locale=detected.value,
                source="detected",
            )
            return detected

        logger.info(
            "locale_resolved",
            phone_hash=log_fingerprint(phone_number),
            locale=cls.DEFAULT_LOCALE.value,
            source="default",
        )
        return cls.DEFAULT_LOCALE
