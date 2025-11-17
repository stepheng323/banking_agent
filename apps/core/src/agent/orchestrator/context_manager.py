"""Context and state management for the orchestrator."""

from typing import Any, Optional
import json

from shared.cache import UserContextCacheService
from shared.repositories.user_repository import UserRepository
from shared.cache.redis_client import RedisClient
from shared.database.models import Account
from shared.utils.serialization import sqlalchemy_to_dict
from apps.core.src.agent.models.classification import ClassificationResult


class OrchestratorContextManager:
    """Manages user context, conversation state, and response caching."""

    def __init__(
        self,
        user_cache: UserContextCacheService,
        user_repo: Optional[UserRepository] = None,
    ) -> None:
        self.user_cache = user_cache
        self.user_repo = user_repo

    async def load_user_context(self, phone_number: str) -> dict[str, Any]:
        """Load user context from cache or database."""
        cached = await self.user_cache.get(phone_number)
        if cached:
            return cached

        profile = None
        accounts: list[Account] = []
        if self.user_repo:
            profile = self.user_repo.get_by_phone(phone_number)

        safe_profile: dict[str, Any] | None = sqlalchemy_to_dict(
            profile) if profile is not None else None

        context = {
            "profile": safe_profile,
            "accounts": accounts,
        }
        await self.user_cache.set(phone_number, context)
        return context

    async def get_conversation_state(self, phone_number: str) -> Optional[dict[str, Any]]:
        """Get the current conversation/flow state for this user."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:conversation_state"
            data = await redis_client.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    async def get_last_response(self, phone_number: str) -> Optional[str]:
        """Get last assistant response from Redis (fast, for LLM context)."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_response"
            return await redis_client.get(key)
        except Exception:
            pass
        return None

    async def save_last_response(self, phone_number: str, response: str) -> None:
        """Save last assistant response to Redis (non-blocking, for LLM context)."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_response"
            await redis_client.set(key, response, ex=3600)
        except Exception:
            pass

    async def save_classification_result(self, phone_number: str, result: ClassificationResult) -> None:
        """Save classification result to Redis for use by transaction flows."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_classification"
            await redis_client.set(key, result.model_dump_json(), ex=3600)
        except Exception:
            pass
