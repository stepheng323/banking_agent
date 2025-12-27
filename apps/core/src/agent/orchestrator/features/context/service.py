"""Context and state management for the orchestrator."""

import asyncio
import json
from typing import Any

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger
from shared.utils.serialization import sqlalchemy_to_dict

logger = get_logger(__name__)


class OrchestratorContextManager:
    """Manages user context, conversation state, and response caching."""

    def __init__(
        self,
        user_repo: UserRepository | None = None,
        beneficiary_repo: BeneficiaryRepository | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.beneficiary_repo = beneficiary_repo
        self.data_cache = UserDataCache()

    async def load_user_context(self, phone_number: str, user: Any | None = None) -> dict[str, Any]:
        """
        Load user context from cache or database.

        Uses UserDataCache (Redis) for structured data with TTL.

        Args:
            phone_number: User's phone number
            user: Optional pre-fetched user object to avoid duplicate database queries
        """
        cached_data = await self.data_cache.get_all_user_data(phone_number)

        if cached_data["profile"]:
            return {
                "profile": cached_data["profile"],
                "accounts": cached_data["accounts"] or [],
                "beneficiaries": cached_data["beneficiaries"] or [],
            }

        def fetch_db_data():
            """Fetch user and accounts in a separate thread to avoid blocking."""
            current_profile = user
            if current_profile is None and self.user_repo:
                current_profile = self.user_repo.get_by_phone(phone_number)

            current_accounts = []
            if current_profile:
                current_accounts = list(current_profile.accounts)

            current_beneficiaries = []
            if current_profile and self.beneficiary_repo:
                user_id = str(current_profile.id)
                current_beneficiaries = self.beneficiary_repo.get_by_user(user_id)

            return current_profile, current_accounts, current_beneficiaries

        profile, accounts, beneficiaries = await asyncio.to_thread(fetch_db_data)

        safe_profile: dict[str, Any] | None = (
            sqlalchemy_to_dict(profile) if profile is not None else None
        )

        safe_accounts = [sqlalchemy_to_dict(acc) for acc in accounts]

        safe_beneficiaries = [sqlalchemy_to_dict(ben) for ben in beneficiaries]

        context = {
            "profile": safe_profile,
            "accounts": safe_accounts,
            "beneficiaries": safe_beneficiaries,
        }

        if safe_profile:
            await self.data_cache.set_user_profile(phone_number, safe_profile)
        if safe_accounts:
            await self.data_cache.set_accounts(phone_number, safe_accounts)
        if safe_beneficiaries:
            await self.data_cache.set_beneficiaries(phone_number, safe_beneficiaries)

        return context

    async def get_recent_transactions(
        self, phone_number: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """
        Get user's recent transactions for smart context.

        Returns a simplified list of recent transactions that can be passed
        to LLM extraction prompts to enable context-aware responses.
        """
        from shared.repositories.unit_of_work import UnitOfWork

        try:

            def fetch_transactions():
                with UnitOfWork() as uow:
                    user = uow.users.get_by_phone(phone_number)
                    if not user:
                        return []

                    txns = uow.transactions.get_by_user(str(user.id), limit=limit)
                    return [
                        {
                            "type": t.transaction_type,
                            "amount": float(t.amount) if t.amount else 0,
                            "recipient_name": t.recipient_name,
                            "recipient_account": t.recipient_account_number,
                            "recipient_bank": t.recipient_bank_name,
                            "status": t.status,
                            "date": t.created_at.isoformat() if t.created_at else None,
                        }
                        for t in txns
                    ]

            return await asyncio.to_thread(fetch_transactions)
        except Exception as e:
            logger.error("get_recent_transactions_error", phone=phone_number, error=str(e))
            return []

    async def get_conversation_state(self, phone_number: str) -> dict[str, Any] | None:
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

    async def clear_conversation_state(self, phone_number: str) -> None:
        """Clear conversation state for this user."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
        except Exception:
            logger.error("error_clearing")

    async def get_last_response(self, phone_number: str) -> str | None:
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

    async def save_message_id(self, phone_number: str, message_id: str) -> None:
        """Save current message_id to Redis for typing indicator support."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:current_message_id"
            await redis_client.set(key, message_id, ex=300)  # 5 min TTL
        except Exception:
            pass

    async def get_message_id(self, phone_number: str) -> str | None:
        """Get current message_id from Redis for typing indicator."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:current_message_id"
            return await redis_client.get(key)
        except Exception:
            return None

    async def save_classification_result(
        self, phone_number: str, result: ClassificationResult
    ) -> None:
        """Save classification result to Redis for use by transaction flows."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_classification"
            await redis_client.set(key, result.model_dump_json(), ex=3600)
        except Exception:
            pass

    async def get_conversation_history(
        self, phone_number: str, limit: int = 10
    ) -> list[dict[str, str]]:
        """Get recent conversation history."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:chat_history"
            items = await redis_client.lrange(key, -limit, -1)
            return [json.loads(item) for item in items]
        except Exception:
            return []

    async def add_conversation_turn(self, phone_number: str, role: str, content: str) -> None:
        """Add a message to conversation history."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:chat_history"
            message = json.dumps({"role": role, "content": content})
            await redis_client.rpush(key, message)
            await redis_client.ltrim(key, -50, -1)
            await redis_client.expire(key, 86400)
        except Exception:
            pass

    async def get_user_language(self, phone_number: str) -> str | None:
        """Get user's preferred language."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:language"
            return await redis_client.get(key)
        except Exception:
            return None

    async def set_user_language(self, phone_number: str, language: str) -> None:
        """Set user's preferred language."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:language"
            await redis_client.set(key, language, ex=2592000)  # 30 days
        except Exception:
            pass

    async def load_context_parallel(
        self, phone_number: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str | None, str | None]:
        """
        Load all context data in parallel using Redis pipeline for optimal performance.

        Fetches multiple Redis keys in a single round trip:
        - user context (ctx)
        - conversation_state
        - last_response
        - beneficiary_suggestion
        - language
        - chat_history

        Args:
            phone_number: User's phone number

        Returns:
            Tuple of (user_ctx, conversation_state, last_response, suggestion_data)
        """
        try:
            redis_client = RedisClient.get_client()
            keys = [
                f"user:{phone_number}:conversation_state",
                f"user:{phone_number}:last_response",
                f"user:{phone_number}:beneficiary_suggestion",
                f"user:{phone_number}:language",
                f"user:{phone_number}:chat_history",
            ]

            pipe = redis_client.pipeline()
            for key in keys[0:4]:  # Get values for first 4 keys
                pipe.get(key)

            pipe.lrange(keys[4], -10, -1)

            results = await pipe.execute()

            user_ctx = await self.load_user_context(phone_number)

            conversation_state = None
            if results[0]:
                try:
                    conversation_state = json.loads(results[0])
                except (json.JSONDecodeError, TypeError):
                    pass

            last_response = results[1] if results[1] else None
            suggestion_data = results[2] if results[2] else None
            language = results[3] if results[3] else None

            history_raw = results[4] if results[4] else []
            history = []
            try:
                history = [json.loads(item) for item in history_raw]
            except Exception:
                pass

            user_ctx["language"] = language
            user_ctx["history"] = history

            return user_ctx, conversation_state, last_response, suggestion_data

        except Exception:
            logger.error("error_in_parallel_context")
            user_ctx = await self.load_user_context(phone_number)
            conversation_state = await self.get_conversation_state(phone_number)
            last_response = await self.get_last_response(phone_number)
            language = await self.get_user_language(phone_number)
            history = await self.get_conversation_history(phone_number)

            try:
                redis_client = RedisClient.get_client()
                suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                suggestion_data = await redis_client.get(suggestion_key)
            except Exception:
                suggestion_data = None

            user_ctx["language"] = language
            user_ctx["history"] = history

            return user_ctx, conversation_state, last_response, suggestion_data
