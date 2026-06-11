"""Support context manager for Redis persistence."""

import json
import time

import redis.asyncio as redis

from banking.support.models import SupportContext, SupportIntent
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SUPPORT_CONTEXT_PREFIX = "support_context:"
CONTEXT_TTL_SECONDS = 86400 * 7


class SupportContextManager:
    """Manages SupportContext persistence in Redis."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _key(self, user_id: str) -> str:
        return f"{SUPPORT_CONTEXT_PREFIX}{user_id}"

    @staticmethod
    def _prune_expired_ephemeral_context(context: SupportContext) -> bool:
        now = time.time()
        changed = False
        pending_reference = context.pending_reference
        if pending_reference is not None and pending_reference.expires_at_ts is not None:
            if pending_reference.expires_at_ts <= now:
                context.pending_reference = None
                changed = True
        receipt_thread_state = context.receipt_thread_state
        if receipt_thread_state is not None and receipt_thread_state.expires_at_ts is not None:
            if receipt_thread_state.expires_at_ts <= now:
                context.receipt_thread_state = None
                changed = True
        return changed

    async def get(self, user_id: str) -> SupportContext:
        """Get support context for a user. Returns empty context if not found."""
        try:
            key = self._key(user_id)
            data = await self.redis.get(key)

            if data:
                parsed = json.loads(data)
                if parsed.get("last_issue_intent"):
                    try:
                        parsed["last_issue_intent"] = SupportIntent(parsed["last_issue_intent"])
                    except ValueError:
                        parsed["last_issue_intent"] = None

                context = SupportContext(**parsed)
                if self._prune_expired_ephemeral_context(context):
                    await self.save(user_id, context)
                return context

            return SupportContext()

        except Exception as e:
            logger.warning("support_context_get_error", user_id=user_id, error=str(e))
            return SupportContext()

    async def save(self, user_id: str, context: SupportContext) -> None:
        """Save support context for a user."""
        try:
            key = self._key(user_id)
            data = context.model_dump()
            if data.get("last_issue_intent"):
                data["last_issue_intent"] = (
                    data["last_issue_intent"].value
                    if hasattr(data["last_issue_intent"], "value")
                    else str(data["last_issue_intent"])
                )

            await self.redis.setex(key, CONTEXT_TTL_SECONDS, json.dumps(data))
        except Exception as e:
            logger.error("support_context_save_error", user_id=user_id, error=str(e))

    async def clear(self, user_id: str) -> None:
        """Clear support context for a user."""
        try:
            await self.redis.delete(self._key(user_id))
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
        """Reset context after resolution or ticket creation."""
        context = await self.get(user_id)

        if ticket_id:
            context.last_ticket_id = ticket_id
        if transaction_ref:
            context.last_transaction_ref = transaction_ref

        context.attempts = 0
        context.last_support_step = "resolved" if not ticket_id else "ticket_created"
        context.pending_reference = None
        context.receipt_thread_state = None

        await self.save(user_id, context)
        return context
