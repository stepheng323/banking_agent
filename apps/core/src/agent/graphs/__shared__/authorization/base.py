"""Base class for transaction authorization using Template Method pattern."""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from sqlalchemy.exc import IntegrityError

from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.auth import AuthorizationService
from shared.utils.logging import get_logger

from .pin_handler import handle_pin_failure
from .responses import (
    build_awaiting_pin_response,
    build_max_retries_response,
    build_pin_failed_response,
    build_session_expired_response,
)

logger = get_logger(__name__)

StateT = TypeVar("StateT", bound=dict[str, Any])


class AuthorizationBase(ABC, Generic[StateT]):
    """
    Abstract base class for transaction authorization.

    Uses Template Method pattern:
    - Common logic: PIN verification, transaction persistence, cleanup
    - Subclass-specific: transaction params, queue message, success response
    """

    def __init__(
        self,
        redis_client: Redis,
        queue: RedisQueue,
        authorization_service: AuthorizationService | None = None,
    ):
        self.redis = redis_client
        self.queue = queue
        self.auth_service = authorization_service or AuthorizationService(redis_client=redis_client)

    async def authorize(self, state: StateT) -> StateT:
        """Template method - orchestrates authorization flow."""
        phone_number = state.get("phone_number")
        idem_key = state.get("idempotency_key")

        logger.info(
            "authorization_started",
            flow_type=self.get_flow_type(),
            phone=phone_number,
            idem_key=idem_key[:20] if idem_key else None,
        )

        if not idem_key:
            logger.warning("authorization_no_idem_key", phone=phone_number)
            result = await build_session_expired_response(state)
            return self._add_status_fields(result, "failed")

        pin_result = await self.auth_service.get_pin_verification_result(idem_key)
        pin_verified_in_state = state.get("pin_verified")

        if not pin_result and not pin_verified_in_state:
            logger.debug("authorization_awaiting_pin", idem_key=idem_key[:20])
            return await build_awaiting_pin_response(state)

        if pin_result and not pin_result.verified and not pin_verified_in_state:
            failure = handle_pin_failure(pin_result)

            if failure.exceeded_max:
                await self._cleanup_pending_data(phone_number)
                result = await build_max_retries_response(state, failure.error or "")
                return self._add_status_fields(result, "failed", failure.retry_count)

            result = await build_pin_failed_response(state, failure.error or "", failure.retry_count)
            return result

        try:
            user_id = self._get_user_id(state, pin_result)
            if not user_id:
                logger.warning("authorization_no_user_id", phone=phone_number)
                result = await build_session_expired_response(state)
                return self._add_status_fields(result, "failed")

            transaction_params = self.build_transaction_params(state, user_id)

            # I dey fear user input, i just sey make i check again
            amount = transaction_params.get("amount")
            if amount is None or (isinstance(amount, (int, float)) and amount <= 0):
                logger.error(
                    "authorization_invalid_amount",
                    flow_type=self.get_flow_type(),
                    amount=amount,
                    phone=phone_number,
                )
                return await self._handle_error(state, ValueError("Invalid transaction amount"))

            transaction_id = self.persist_transaction(transaction_params, user_id, idem_key)

            await self.enqueue_for_execution(state, transaction_id, transaction_params, user_id)

            logger.info(
                "authorization_transaction_queued",
                flow_type=self.get_flow_type(),
                transaction_id=transaction_id,
                phone=phone_number,
            )

            await self._cleanup_after_auth(phone_number, idem_key)

            retry_count = pin_result.retry_count if pin_result else 0
            return self.build_success_response(state, transaction_id, retry_count)

        except Exception as e:
            logger.error(
                "authorization_error",
                flow_type=self.get_flow_type(),
                error=str(e),
                phone=phone_number,
                exc_info=True,
            )
            return await self._handle_error(state, e)

    def persist_transaction(
        self,
        params: dict[str, Any],
        user_id: str,
        idempotency_key: str,
    ) -> str:
        """
        Persist transaction to database with idempotency handling.

        This is the common transaction creation logic. Subclasses provide
        params via build_transaction_params().
        """
        with UnitOfWork() as uow:
            if not uow.transactions:
                raise ValueError("Transaction repository not available")

            try:
                transaction = uow.transactions.create(
                    user_id=user_id,
                    transaction_type=self.get_transaction_type(),
                    status="pending",
                    idempotency_key=idempotency_key,
                    **params,
                )
                transaction_id = str(transaction.id)
                uow.commit()
                logger.info(
                    f"{self.get_flow_type()}_transaction_created",
                    transaction_id=transaction_id,
                )
                return transaction_id
            except IntegrityError:
                try:
                    uow.rollback()
                except Exception:
                    pass
                if uow.transactions:
                    existing = uow.transactions.get_by_idempotency_key(idempotency_key)
                    if existing:
                        existing_id = str(existing.id)
                        logger.info(
                            f"{self.get_flow_type()}_transaction_exists",
                            transaction_id=existing_id,
                            idempotency_key=idempotency_key,
                        )
                        return existing_id
                raise

    def _get_user_id(self, state: StateT, pin_result) -> str | None:
        """Extract user_id from PIN result or state."""
        if pin_result and pin_result.user_id:
            return pin_result.user_id

        user_profile = state.get("user_profile")
        if isinstance(user_profile, dict):
            return user_profile.get("id")

        return None

    def _add_status_fields(
        self,
        state: StateT,
        status: str,
        retry_count: int = 0,
    ) -> StateT:
        """Add transaction-type-specific status field."""
        status_field = self.get_status_field()
        return {
            **state,
            status_field: status,
            "pin_retry_count": retry_count,
        }

    async def _cleanup_pending_data(self, phone_number: str) -> None:
        """Clean up pending transaction data."""
        pending_key = self.get_pending_data_key(phone_number)
        if pending_key:
            await self.redis.delete(pending_key)

    async def _cleanup_after_auth(self, phone_number: str, idem_key: str) -> None:
        """Clean up after successful authorization."""
        pin_key = f"transaction:pin_verified:{idem_key}"
        await self.redis.delete(pin_key)

        prev_key = f"{self.get_flow_type()}:prev:session:{phone_number}"
        await self.redis.delete(prev_key)

    async def _handle_error(self, state: StateT, error: Exception) -> StateT:
        """Handle authorization error."""
        from .responses import build_authorization_error_response

        return await build_authorization_error_response(
            state,
            "Failed to process authorization. Please try again.",
            self.get_error_intent(),
        )

    @abstractmethod
    def get_flow_type(self) -> str:
        """Return flow type identifier: 'transfer', 'airtime', 'data', etc."""
        pass

    @abstractmethod
    def get_transaction_type(self) -> str:
        """Return transaction type for database: 'transfer', 'airtime', 'data'."""
        pass

    @abstractmethod
    def get_status_field(self) -> str:
        """Return the status field name: 'transfer_status', 'airtime_status', etc."""
        pass

    @abstractmethod
    def get_pending_data_key(self, phone_number: str) -> str | None:
        """Return Redis key for pending transaction data, or None."""
        pass

    @abstractmethod
    def get_error_intent(self):
        """Return ResponseIntent for errors."""
        pass

    @abstractmethod
    def build_transaction_params(self, state: StateT, user_id: str) -> dict[str, Any]:
        """
        Build transaction parameters for persistence.

        Returns dict with keys matching Transaction model fields:
        - amount, currency
        - source_account_id, source_account_number, source_bank_name
        - recipient_account_number, recipient_bank_code, recipient_bank_name, recipient_name
        - narration
        """
        pass

    @abstractmethod
    async def enqueue_for_execution(
        self,
        state: StateT,
        transaction_id: str,
        transaction_params: dict[str, Any],
        user_id: str,
    ) -> None:
        """Enqueue transaction for async execution."""
        pass

    @abstractmethod
    def build_success_response(self, state: StateT, transaction_id: str, retry_count: int) -> StateT:
        """Build success response state."""
        pass
