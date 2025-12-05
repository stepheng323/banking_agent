"""Context and state management for the orchestrator."""

from typing import Any, Optional
import json
import asyncio

from shared.repositories.user_repository import UserRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.cache.redis_client import RedisClient
from shared.database.models import Account
from shared.utils.serialization import sqlalchemy_to_dict
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.services.user_data_cache import UserDataCache


class OrchestratorContextManager:
    """Manages user context, conversation state, and response caching."""

    def __init__(
        self,
        user_repo: Optional[UserRepository] = None,
        beneficiary_repo: Optional[BeneficiaryRepository] = None,
    ) -> None:
        self.user_repo = user_repo
        self.beneficiary_repo = beneficiary_repo
        self.data_cache = UserDataCache()

    async def load_user_context(self, phone_number: str, user: Optional[Any] = None) -> dict[str, Any]:
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
                current_beneficiaries = self.beneficiary_repo.get_by_user(current_profile.id)

            return current_profile, current_accounts, current_beneficiaries

        profile, accounts, beneficiaries = await asyncio.to_thread(fetch_db_data)

        safe_profile: dict[str, Any] | None = sqlalchemy_to_dict(
            profile) if profile is not None else None
            
        safe_accounts = [
            sqlalchemy_to_dict(acc) for acc in accounts
        ]

        safe_beneficiaries = [
            sqlalchemy_to_dict(ben) for ben in beneficiaries
        ]

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

    async def clear_conversation_state(self, phone_number: str) -> None:
        """Clear conversation state for this user."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
        except Exception as e:
            print(f"⚠️  Error clearing conversation_state: {e}")

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

    async def load_context_parallel(self, phone_number: str) -> tuple[dict[str, Any], Optional[dict[str, Any]], Optional[str], Optional[str]]:
        """
        Load all context data in parallel using Redis pipeline for optimal performance.
        
        Fetches multiple Redis keys in a single round trip:
        - user context (ctx)
        - conversation_state
        - last_response
        - beneficiary_suggestion
        
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
            ]
            
            # Use pipeline to fetch all keys in parallel
            pipe = redis_client.pipeline()
            for key in keys:
                pipe.get(key)
            results = await pipe.execute()
            
            # Load user context separately (uses UserDataCache)
            user_ctx = await self.load_user_context(phone_number)
            
            # Parse conversation_state
            conversation_state = None
            if results[0]:
                try:
                    conversation_state = json.loads(results[0])
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # last_response is already a string or None
            last_response = results[1] if results[1] else None
            
            # suggestion_data is JSON string or None
            suggestion_data = results[2] if results[2] else None
            
            return user_ctx, conversation_state, last_response, suggestion_data
            
        except Exception as e:
            # Fallback to individual calls on error
            print(f"⚠️  Error in parallel context loading: {e}, falling back to sequential")
            user_ctx = await self.load_user_context(phone_number)
            conversation_state = await self.get_conversation_state(phone_number)
            last_response = await self.get_last_response(phone_number)
            
            try:
                redis_client = RedisClient.get_client()
                suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                suggestion_data = await redis_client.get(suggestion_key)
            except Exception:
                suggestion_data = None
                
            return user_ctx, conversation_state, last_response, suggestion_data
