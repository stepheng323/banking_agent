"""Context and state management service."""

from typing import Any, Literal, cast

import apps.chat.src.agent.orchestrator.context.context_parallel_loader as context_parallel_loader
import apps.chat.src.agent.orchestrator.context.context_redis_state as context_redis_state
from apps.chat.src.agent.orchestrator.context.context_user_data import hydrate_user_context_from_cache_snapshot
from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.identity.repositories.user_repository import UserRepository
from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.models import LanguageDetectionSignal
from shared.cache.user_data import UserDataCache
from shared.utils.logging import get_logger, log_fingerprint, log_orchestrator_diagnostic

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
        log_orchestrator_diagnostic(
            logger,
            "perf_timer_latency",
            gate=span,
            span=span,
            duration_ms=round(duration_ms, 2),
            path_label=path_label,
            phone_hash=log_fingerprint(phone_number),
        )

    @staticmethod
    def _recent_transaction_display_name(transaction: Any) -> str | None:
        transaction_type = str(getattr(transaction, "transaction_type", "") or "").strip().lower()
        target_phone = str(getattr(transaction, "target_phone_number", "") or "").strip()
        mobile_network = str(getattr(transaction, "mobile_network", "") or "").strip()
        plan_name = str(getattr(transaction, "biller_item_name", "") or "").strip()
        recipient_name = str(getattr(transaction, "recipient_name", "") or "").strip()
        recipient_account = str(getattr(transaction, "recipient_account_number", "") or "").strip()
        recipient_bank = str(getattr(transaction, "recipient_bank_name", "") or "").strip()

        if transaction_type == "airtime":
            phone = target_phone or recipient_account
            network = mobile_network or recipient_bank
            if phone and network:
                return f"{phone} ({network})"
            return phone or network or recipient_name or None
        if transaction_type == "data":
            plan = plan_name or recipient_name
            phone = target_phone or recipient_account
            if plan and phone:
                return f"{plan} for {phone}"
            return plan or phone or mobile_network or None
        return recipient_name or None

    async def load_user_context(
        self,
        phone_number: str,
        user: Any | None = None,
        *,
        cached_data: dict[str, Any] | None = None,
        path_label: str = "planner_path",
        profile_mode: Literal["full", "minimal"] = "full",
        account_mode: Literal["full", "cache_only"] = "full",
        beneficiary_mode: Literal["full", "cache_only"] = "full",
    ) -> dict[str, Any]:
        """
        Load user context from cache or database.

        Uses UserDataCache (Redis) for structured data with TTL.

        Args:
            phone_number: User's phone number
            user: Optional pre-fetched user object to avoid duplicate database queries
        """
        cached_data = cached_data or await self.data_cache.get_all_user_data(phone_number)
        return await hydrate_user_context_from_cache_snapshot(
            phone_number,
            user=user,
            cached_data=cached_data,
            path_label=path_label,
            profile_mode=profile_mode,
            account_mode=account_mode,
            beneficiary_mode=beneficiary_mode,
            user_repo=self.user_repo,
            account_repo=self.account_repo,
            beneficiary_repo=self.beneficiary_repo,
            data_cache=self.data_cache,
            logger=logger,
            log_latency_span=self._log_latency_span,
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
                        "recipient_name": self._recent_transaction_display_name(t),
                        "recipient_account": t.recipient_account_number,
                        "recipient_bank": t.recipient_bank_name,
                        "target_phone_number": getattr(t, "target_phone_number", None),
                        "mobile_network": getattr(t, "mobile_network", None),
                        "biller_item_name": getattr(t, "biller_item_name", None),
                        "status": t.status,
                        "date": t.created_at.isoformat() if t.created_at else None,
                    }
                    for t in txns
                ]
        except Exception as e:
            logger.error(
                "get_recent_transactions_error",
                phone_hash=log_fingerprint(phone_number),
                error_type=type(e).__name__,
            )
            return []

        return []

    async def get_conversation_state(self, phone_number: str) -> dict[str, Any] | None:
        """Get the current conversation/flow state for this user."""
        return await context_redis_state.get_conversation_state(phone_number)

    async def clear_conversation_state(self, phone_number: str) -> None:
        """Clear conversation state for this user."""
        await context_redis_state.clear_conversation_state(phone_number)

    async def get_last_response(self, phone_number: str) -> str | None:
        """Get last assistant response from Redis (fast, for LLM context)."""
        return await context_redis_state.get_last_response(phone_number)

    async def save_last_response(self, phone_number: str, response: str) -> None:
        """Save last assistant response to Redis (non-blocking, for LLM context)."""
        await context_redis_state.save_last_response(phone_number, response)

    async def save_message_id(self, phone_number: str, message_id: str) -> None:
        """Save current message_id to Redis for typing indicator support."""
        await context_redis_state.save_message_id(phone_number, message_id)

    async def claim_inbound_message(
        self,
        phone_number: str,
        message_id: str,
        ttl_seconds: int = 86400,
    ) -> bool:
        """Claim an inbound message id once to suppress duplicate deliveries."""
        return await context_redis_state.claim_inbound_message(phone_number, message_id, ttl_seconds)

    async def release_inbound_message_claim(self, phone_number: str, message_id: str) -> None:
        """Release a previously-claimed inbound message id on failure."""
        await context_redis_state.release_inbound_message_claim(phone_number, message_id)

    async def get_message_id(self, phone_number: str) -> str | None:
        """Get current message_id from Redis for typing indicator."""
        return await context_redis_state.get_message_id(phone_number)

    async def get_conversation_history(self, phone_number: str, limit: int = 3) -> list[dict[str, Any]]:
        """Get recent conversation history."""
        return await context_redis_state.get_conversation_history(phone_number, limit)

    async def add_conversation_turn(
        self,
        phone_number: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a message to conversation history."""
        await context_redis_state.add_conversation_turn(phone_number, role, content, metadata=metadata)

    async def get_user_language(self, phone_number: str) -> str | None:
        """Get user's preferred language."""
        try:
            locale = await LocaleManager.get_locale(phone_number)
            return cast(str | None, (locale.value if locale else None))
        except Exception as e:
            logger.warning(
                "get_user_language_error",
                phone_hash=log_fingerprint(phone_number),
                error_type=type(e).__name__,
            )
            return None

    async def set_user_language(self, phone_number: str, language: str) -> None:
        """Set user's preferred language."""
        try:
            await LocaleManager.set_locale(phone_number, language, source="context_set_user_language")
        except Exception as e:
            logger.warning(
                "set_user_language_error",
                phone_hash=log_fingerprint(phone_number),
                error_type=type(e).__name__,
            )

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
        user: Any | None = None,
        profile_mode: Literal["full", "minimal"] = "full",
        account_mode: Literal["full", "cache_only"] = "full",
        beneficiary_mode: Literal["full", "cache_only"] = "full",
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
        return await context_parallel_loader.load_context_parallel(
            phone_number,
            path_label=path_label,
            user=user,
            profile_mode=profile_mode,
            account_mode=account_mode,
            beneficiary_mode=beneficiary_mode,
            user_repo=self.user_repo,
            account_repo=self.account_repo,
            beneficiary_repo=self.beneficiary_repo,
            data_cache=self.data_cache,
            logger=logger,
            log_latency_span=self._log_latency_span,
            load_user_context_fallback=self.load_user_context,
            get_user_language=self.get_user_language,
        )

    async def get_mandate_warning_count(self, phone_number: str) -> int:
        """Get the number of times mandate warning has been shown recently."""
        return await context_redis_state.get_mandate_warning_count(phone_number)

    async def increment_mandate_warning_count(self, phone_number: str, ttl: int = 300) -> int:
        """Increment mandate warning count and set TTL."""
        return await context_redis_state.increment_mandate_warning_count(phone_number, ttl)
