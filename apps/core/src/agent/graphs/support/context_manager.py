"""Support context manager for Redis persistence."""

import json
from typing import Any

import redis.asyncio as redis

from apps.core.src.agent.graphs.support.models import SupportContext, SupportIntent
from shared.utils.logging import get_logger

logger = get_logger(__name__)


# Redis key prefix
SUPPORT_CONTEXT_PREFIX = "support_context:"
CONTEXT_TTL_SECONDS = 86400 * 7  # 7 days


class SupportContextManager:
    """Manages SupportContext persistence in Redis."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _key(self, user_id: str) -> str:
        """Generate Redis key for user's support context."""
        return f"{SUPPORT_CONTEXT_PREFIX}{user_id}"

    async def get(self, user_id: str) -> SupportContext:
        """
        Get support context for a user.
        Returns empty context if not found.
        """
        try:
            key = self._key(user_id)
            data = await self.redis.get(key)
            
            if data:
                parsed = json.loads(data)
                # Handle enum serialization
                if parsed.get("last_issue_intent"):
                    try:
                        parsed["last_issue_intent"] = SupportIntent(parsed["last_issue_intent"])
                    except ValueError:
                        parsed["last_issue_intent"] = None
                
                return SupportContext(**parsed)
            
            return SupportContext()
        
        except Exception as e:
            logger.warning("support_context_get_error", user_id=user_id, error=str(e))
            return SupportContext()

    async def save(self, user_id: str, context: SupportContext) -> None:
        """Save support context for a user."""
        try:
            key = self._key(user_id)
            
            # Serialize with enum handling
            data = context.model_dump()
            if data.get("last_issue_intent"):
                data["last_issue_intent"] = data["last_issue_intent"].value if hasattr(data["last_issue_intent"], "value") else str(data["last_issue_intent"])
            
            await self.redis.setex(key, CONTEXT_TTL_SECONDS, json.dumps(data))
            
            logger.debug(
                "support_context_saved",
                user_id=user_id,
                attempts=context.attempts,
                last_step=context.last_support_step,
            )
        
        except Exception as e:
            logger.error("support_context_save_error", user_id=user_id, error=str(e))

    async def clear(self, user_id: str) -> None:
        """Clear support context for a user."""
        try:
            key = self._key(user_id)
            await self.redis.delete(key)
            logger.debug("support_context_cleared", user_id=user_id)
        except Exception as e:
            logger.warning("support_context_clear_error", user_id=user_id, error=str(e))

    async def increment_attempts(self, user_id: str) -> SupportContext:
        """Increment attempts counter and return updated context."""
        context = await self.get(user_id)
        context.attempts += 1
        await self.save(user_id, context)
        return context

    async def reset_on_resolution(
        self,
        user_id: str,
        ticket_id: str | None = None,
        transaction_ref: str | None = None,
    ) -> SupportContext:
        """
        Reset context after resolution or ticket creation.
        Preserves last refs for "any update?" queries.
        """
        context = await self.get(user_id)
        
        if ticket_id:
            context.last_ticket_id = ticket_id
        if transaction_ref:
            context.last_transaction_ref = transaction_ref
        
        context.attempts = 0
        context.last_support_step = "resolved" if not ticket_id else "ticket_created"
        
        await self.save(user_id, context)
        return context
