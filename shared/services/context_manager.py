"""Context and state management service."""

import asyncio
import json
import time
from typing import Any, cast

from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.i18n import LanguageDetectionSignal, LocaleManager
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger
from shared.utils.serialization import sqlalchemy_to_dict

logger = get_logger(__name__)


class ContextManager:
    """Manages user context, conversation state, and response caching."""

    def __init__(
        self,
        user_repo: UserRepository | None = None,
        beneficiary_repo: BeneficiaryRepository | None = None,
        account_repo: AccountRepository | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.data_cache = UserDataCache()

    @staticmethod
    def _log_latency_span(*, span: str, duration_ms: float, phone_number: str, path_label: str) -> None:
        logger.info(
            "perf_timer_latency",
            gate=span,
            span=span,
            duration_ms=round(duration_ms, 2),
            path_label=path_label,
            phone_number=phone_number,
        )

    @staticmethod
    def _serialize_profile(profile_obj: Any) -> tuple[dict[str, Any] | None, str | None]:
        if isinstance(profile_obj, dict):
            raw_user_id = profile_obj.get("id")
            user_id = str(raw_user_id) if raw_user_id else None
            return dict(profile_obj), user_id
        if profile_obj is not None:
            return sqlalchemy_to_dict(profile_obj), str(profile_obj.id)
        return None, None

    @staticmethod
    def _serialize_rows(rows: list[Any]) -> list[dict[str, Any]]:
        return [dict(row) if isinstance(row, dict) else sqlalchemy_to_dict(row) for row in rows]

    async def _hydrate_user_context_from_cache_snapshot(
        self,
        phone_number: str,
        *,
        user: Any | None = None,
        cached_data: dict[str, Any],
        path_label: str,
    ) -> dict[str, Any]:
        cache_profile = cached_data.get("profile")
        cache_accounts = cached_data.get("accounts")
        cache_beneficiaries = cached_data.get("beneficiaries")

        cache_status = "miss"
        cache_hits = sum(v is not None for v in (cache_profile, cache_accounts, cache_beneficiaries))
        if cache_hits == 3:
            cache_status = "hit"
        elif cache_hits > 0:
            cache_status = "partial_hit"
        logger.info("context_user_data_cache", phone=phone_number, status=cache_status)

        if cache_hits == 3:
            return {
                "profile": cache_profile,
                "accounts": cache_accounts,
                "beneficiaries": cache_beneficiaries,
            }

        profile_obj = user if user is not None else cache_profile
        if profile_obj is None and self.user_repo:
            profile_start = time.perf_counter()
            profile_obj = await self.user_repo.get_by_phone(phone_number)
            self._log_latency_span(
                span="context_profile_fetch",
                duration_ms=(time.perf_counter() - profile_start) * 1000,
                phone_number=phone_number,
                path_label=path_label,
            )

        safe_profile, user_id = self._serialize_profile(profile_obj)

        async def _timed_fetch(label: str, op: Any) -> tuple[str, Any]:
            fetch_start = time.perf_counter()
            result = await op
            self._log_latency_span(
                span=f"context_{label}_fetch",
                duration_ms=(time.perf_counter() - fetch_start) * 1000,
                phone_number=phone_number,
                path_label=path_label,
            )
            return label, result

        accounts: list[Any] = list(cache_accounts) if cache_accounts is not None else []
        beneficiaries: list[Any] = list(cache_beneficiaries) if cache_beneficiaries is not None else []

        fetch_ops: list[tuple[str, Any]] = []
        if user_id and cache_accounts is None and self.account_repo:
            fetch_ops.append(("accounts", _timed_fetch("accounts", self.account_repo.get_by_user(user_id))))
        if user_id and cache_beneficiaries is None and self.beneficiary_repo:
            fetch_ops.append(
                ("beneficiaries", _timed_fetch("beneficiaries", self.beneficiary_repo.get_by_user(user_id)))
            )

        if fetch_ops:
            labels = [label for label, _ in fetch_ops]
            results = await asyncio.gather(*[op for _, op in fetch_ops], return_exceptions=True)
            for label, result in zip(labels, results, strict=False):
                if isinstance(result, Exception):
                    logger.warning(
                        "context_user_data_fetch_error",
                        phone=phone_number,
                        field=label,
                        error=str(result),
                    )
                    continue
                _, value = result
                if label == "accounts":
                    accounts = list(value)
                else:
                    beneficiaries = list(value)

        safe_accounts = self._serialize_rows(accounts)
        safe_beneficiaries = self._serialize_rows(beneficiaries)

        cache_profile_write = safe_profile is not None and cache_profile is None
        cache_accounts_write = bool(safe_accounts) and cache_accounts is None
        cache_beneficiaries_write = cache_beneficiaries is None
        if cache_profile_write or cache_accounts_write or cache_beneficiaries_write:
            write_start = time.perf_counter()
            await self.data_cache.set_user_data_snapshot(
                phone_number,
                profile=safe_profile,
                cache_profile=cache_profile_write,
                accounts=safe_accounts,
                cache_accounts=cache_accounts_write,
                beneficiaries=safe_beneficiaries,
                cache_beneficiaries=cache_beneficiaries_write,
            )
            self._log_latency_span(
                span="context_cache_write",
                duration_ms=(time.perf_counter() - write_start) * 1000,
                phone_number=phone_number,
                path_label=path_label,
            )

        return {
            "profile": safe_profile,
            "accounts": safe_accounts,
            "beneficiaries": safe_beneficiaries,
        }

    async def load_user_context(
        self,
        phone_number: str,
        user: Any | None = None,
        *,
        cached_data: dict[str, Any] | None = None,
        path_label: str = "planner_path",
    ) -> dict[str, Any]:
        """
        Load user context from cache or database.

        Uses UserDataCache (Redis) for structured data with TTL.

        Args:
            phone_number: User's phone number
            user: Optional pre-fetched user object to avoid duplicate database queries
        """
        cached_data = cached_data or await self.data_cache.get_all_user_data(phone_number)
        return await self._hydrate_user_context_from_cache_snapshot(
            phone_number,
            user=user,
            cached_data=cached_data,
            path_label=path_label,
        )

    async def get_user_accounts(self, phone_number: str) -> list[dict[str, Any]]:
        """Get user's linked accounts.

        Returns list of account dicts with mandate_status for validation.
        """
        context = await self.load_user_context(phone_number)
        return cast(list[dict[str, Any]], context.get("accounts", []))

    async def get_recent_transactions(self, phone_number: str, limit: int = 5) -> list[dict[str, Any]]:
        """
        Get user's recent transactions for smart context.

        Returns a simplified list of recent transactions that can be passed
        to LLM extraction prompts to enable context-aware responses.
        """
        try:
            async with UnitOfWork() as uow:
                user = await uow.users.get_by_phone(phone_number)
                if not user:
                    return []

                txns = await uow.transactions.get_by_user(str(user.id), limit=limit)
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
        except Exception as e:
            logger.error("get_recent_transactions_error", phone=phone_number, error=str(e))
            return []

        return []

    async def get_conversation_state(self, phone_number: str) -> dict[str, Any] | None:
        """Get the current conversation/flow state for this user."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:conversation_state"
            data = await redis_client.get(key)
            if data:
                return cast(dict[str, Any], json.loads(data))
        except Exception as e:
            logger.warning("get_conversation_state_error", phone=phone_number, error=str(e))
        return None

    async def clear_conversation_state(self, phone_number: str) -> None:
        """Clear conversation state for this user."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
        except Exception as e:
            logger.error("error_clearing_state", phone=phone_number, error=str(e))

    async def get_last_response(self, phone_number: str) -> str | None:
        """Get last assistant response from Redis (fast, for LLM context)."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_response"
            return cast(str | None, await redis_client.get(key))
        except Exception as e:
            logger.warning("get_last_response_error", phone=phone_number, error=str(e))
        return None

    async def save_last_response(self, phone_number: str, response: str) -> None:
        """Save last assistant response to Redis (non-blocking, for LLM context)."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_response"
            await redis_client.set(key, response, ex=3600)
        except Exception as e:
            logger.warning("save_last_response_error", phone=phone_number, error=str(e))

    async def save_message_id(self, phone_number: str, message_id: str) -> None:
        """Save current message_id to Redis for typing indicator support."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:current_message_id"
            await redis_client.set(key, message_id, ex=300)  # 5 min TTL
        except Exception as e:
            logger.warning("save_message_id_error", phone=phone_number, error=str(e))

    async def claim_inbound_message(
        self,
        phone_number: str,
        message_id: str,
        ttl_seconds: int = 86400,
    ) -> bool:
        """Claim an inbound message id once to suppress duplicate deliveries."""
        if not message_id or message_id == "unknown":
            return True
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:inbound_message:{message_id}"
            claimed = await redis_client.set(key, "1", ex=ttl_seconds, nx=True)
            return bool(claimed)
        except Exception as e:
            logger.warning("claim_inbound_message_error", phone=phone_number, message_id=message_id, error=str(e))
            return True

    async def release_inbound_message_claim(self, phone_number: str, message_id: str) -> None:
        """Release a previously-claimed inbound message id on failure."""
        if not message_id or message_id == "unknown":
            return
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:inbound_message:{message_id}"
            await redis_client.delete(key)
        except Exception as e:
            logger.warning("release_inbound_message_error", phone=phone_number, message_id=message_id, error=str(e))

    async def get_message_id(self, phone_number: str) -> str | None:
        """Get current message_id from Redis for typing indicator."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:current_message_id"
            return cast(str | None, await redis_client.get(key))
        except Exception as e:
            logger.warning("get_message_id_error", phone=phone_number, error=str(e))
            return None

    async def get_conversation_history(self, phone_number: str, limit: int = 10) -> list[dict[str, str]]:
        """Get recent conversation history."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:chat_history"
            items = await redis_client.lrange(key, -limit, -1)
            return [json.loads(item) for item in items]
        except Exception as e:
            logger.warning("get_conversation_history_error", phone=phone_number, error=str(e))
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
        except Exception as e:
            logger.warning("add_conversation_turn_error", phone=phone_number, error=str(e))

    async def get_user_language(self, phone_number: str) -> str | None:
        """Get user's preferred language."""
        try:
            locale = await LocaleManager.get_locale(phone_number)
            return cast(str | None, (locale.value if locale else None))
        except Exception as e:
            logger.warning("get_user_language_error", phone=phone_number, error=str(e))
            return None

    async def set_user_language(self, phone_number: str, language: str) -> None:
        """Set user's preferred language."""
        try:
            await LocaleManager.set_locale(phone_number, language, source="context_set_user_language")
        except Exception as e:
            logger.warning("set_user_language_error", phone=phone_number, error=str(e))

    async def update_user_locale(self, phone_number: str, signal: LanguageDetectionSignal) -> str:
        """Update persisted locale using detection/explicit signals."""
        locale = await LocaleManager.update_locale(phone_number, signal)
        return cast(str, locale.value)

    async def set_user_locale_explicit(self, phone_number: str, locale: str) -> str:
        """Set locale immediately from explicit user command."""
        resolved = await LocaleManager.set_locale(phone_number, locale, source="user_command")
        return cast(str, resolved.value)

    async def get_effective_locale(self, phone_number: str, detected_language: str | None = None) -> str:
        """Resolve effective locale from persisted preference and hint."""
        locale = await LocaleManager.get_effective_locale(phone_number, detected_language)
        return cast(str, locale.value)

    async def load_context_parallel(
        self,
        phone_number: str,
        *,
        path_label: str = "planner_path",
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
                f"cache:user:profile:{phone_number}",
                f"cache:user:accounts:{phone_number}",
                f"cache:user:beneficiaries:{phone_number}",
            ]

            cache_fetch_start = time.perf_counter()
            pipe = redis_client.pipeline()
            for key in keys[0:4]:  # Get values for first 4 keys
                pipe.get(key)

            pipe.lrange(keys[4], -10, -1)
            for key in keys[5:8]:
                pipe.get(key)

            results = await pipe.execute()
            self._log_latency_span(
                span="context_cache_fetch",
                duration_ms=(time.perf_counter() - cache_fetch_start) * 1000,
                phone_number=phone_number,
                path_label=path_label,
            )

            cached_user_data: dict[str, Any] = {
                "profile": json.loads(results[5]) if results[5] else None,
                "accounts": json.loads(results[6]) if results[6] else None,
                "beneficiaries": json.loads(results[7]) if results[7] else None,
            }
            user_ctx = await self._hydrate_user_context_from_cache_snapshot(
                phone_number,
                cached_data=cached_user_data,
                path_label=path_label,
            )

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
            user_ctx["detected_language"] = language
            user_ctx["history"] = history

            return user_ctx, conversation_state, last_response, suggestion_data

        except Exception as e:
            logger.error("error_in_parallel_context", phone=phone_number, error=str(e))
            user_ctx = await self.load_user_context(phone_number, path_label=path_label)
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
            user_ctx["detected_language"] = language
            user_ctx["history"] = history

            return user_ctx, conversation_state, last_response, suggestion_data

    async def get_mandate_warning_count(self, phone_number: str) -> int:
        """Get the number of times mandate warning has been shown recently."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:mandate_warning_count"
            count = await redis_client.get(key)
            return int(count) if count else 0
        except Exception as e:
            logger.error("get_mandate_warning_count_error", error=str(e))
            return 0

    async def increment_mandate_warning_count(self, phone_number: str, ttl: int = 300) -> int:
        """Increment mandate warning count and set TTL."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:mandate_warning_count"
            count = await redis_client.incr(key)
            await redis_client.expire(key, ttl)
            return cast(int, count)
        except Exception as e:
            logger.error("increment_mandate_warning_count_error", error=str(e))
            return 1


# Alias for backward compatibility
OrchestratorContextManager = ContextManager
