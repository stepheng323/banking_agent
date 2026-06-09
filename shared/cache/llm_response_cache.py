"""Best-effort Redis cache for selected structured LLM responses."""

from __future__ import annotations

import hashlib
import re
from typing import TypeVar

from pydantic import BaseModel

from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.utils.logging import get_logger

StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)

logger = get_logger(__name__)

_CACHE_SCHEMA_VERSION = "v1"
_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")


def prompt_hash(value: str) -> str:
    """Return a short stable fingerprint for prompt text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_key_part(value: str | None) -> str:
    normalized = _SAFE_KEY_RE.sub("_", (value or "unknown").strip())
    return normalized or "unknown"


def structured_response_cache_enabled(response_type: type[BaseModel]) -> bool:
    """Return whether a response type is allowed to use the LLM response cache."""
    if not settings.llm_response_cache_enabled:
        return False
    return response_type.__name__ in set(settings.llm_response_cache_types)


class StructuredLLMResponseCache:
    """Cache validated Pydantic structured outputs by model and prompt hashes."""

    def __init__(self) -> None:
        self.redis = RedisClient.get_client()

    def key(
        self,
        *,
        response_type: type[BaseModel],
        model: str | None,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Build the Redis key without storing raw prompt content."""
        return ":".join(
            (
                "llm_cache",
                _CACHE_SCHEMA_VERSION,
                _safe_key_part(response_type.__name__),
                _safe_key_part(model),
                prompt_hash(system_prompt),
                prompt_hash(user_prompt),
            )
        )

    async def get(
        self,
        *,
        response_type: type[StructuredResultT],
        model: str | None,
        system_prompt: str,
        user_prompt: str,
    ) -> StructuredResultT | None:
        """Return a cached structured output, deleting invalid entries."""
        cache_key = self.key(
            response_type=response_type,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        try:
            raw = await self.redis.get(cache_key)
            if not raw:
                return None
            try:
                return response_type.model_validate_json(raw)
            except Exception as exc:
                await self.redis.delete(cache_key)
                logger.warning(
                    "llm_response_cache_invalid",
                    response_type=response_type.__name__,
                    model=model,
                    cache_key_hash=prompt_hash(cache_key),
                    error_type=type(exc).__name__,
                )
                return None
        except Exception as exc:
            logger.warning(
                "llm_response_cache_get_failed",
                response_type=response_type.__name__,
                model=model,
                error_type=type(exc).__name__,
            )
            return None

    async def set(
        self,
        *,
        response: BaseModel,
        model: str | None,
        system_prompt: str,
        user_prompt: str,
    ) -> None:
        """Write a structured output to Redis with the configured TTL."""
        cache_key = self.key(
            response_type=type(response),
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        try:
            ttl_seconds = max(1, int(settings.llm_response_cache_ttl_seconds))
            await self.redis.setex(cache_key, ttl_seconds, response.model_dump_json())
            logger.info(
                "llm_response_cache_write",
                response_type=type(response).__name__,
                model=model,
                cache_key_hash=prompt_hash(cache_key),
                ttl_seconds=ttl_seconds,
            )
        except Exception as exc:
            logger.warning(
                "llm_response_cache_write_failed",
                response_type=type(response).__name__,
                model=model,
                error_type=type(exc).__name__,
            )


__all__ = [
    "StructuredLLMResponseCache",
    "prompt_hash",
    "structured_response_cache_enabled",
]
