"""Authorization service for PIN verification across all transaction types."""

import json
from dataclasses import dataclass

from shared.cache.redis_client import RedisClient
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.hash import is_valid_pin_format, verify_hash


@dataclass
class AuthorizationResult:
    """Result of PIN authorization attempt."""

    verified: bool
    user_id: str | None = None
    transaction_type: str | None = None
    error: str | None = None
    retry_count: int = 0
    attempts_remaining: int = 3


class AuthorizationService:
    """Service for handling PIN verification and authorization."""

    def __init__(self, redis_client=None):
        """Initialize authorization service."""
        self.redis_client = redis_client or RedisClient.get_client()

    async def get_transaction_type_from_pending(self, idempotency_key: str, phone_number: str) -> str | None:
        """
        Extract transaction type from pending transaction in Redis.

        Args:
            idempotency_key: Transaction idempotency key
            phone_number: User's phone number

        Returns:
            Transaction type (transfer, airtime, data) or None if not found
        """
        transaction_types = ["transfer", "airtime", "data"]
        for tx_type in transaction_types:
            pending_key = f"user:{phone_number}:pending_{tx_type}"
            pending_data = await self.redis_client.get(pending_key)
            if pending_data:
                try:
                    pending = json.loads(pending_data)
                    if pending.get("idempotency_key") == idempotency_key:
                        return pending.get("transaction_type") or tx_type
                except (json.JSONDecodeError, KeyError):
                    continue
        return None

    async def verify_pin(
        self, phone_number: str, pin: str, idempotency_key: str, transaction_type: str | None = None
    ) -> AuthorizationResult:
        """
        Verify PIN for a transaction.

        Args:
            phone_number: User's phone number
            pin: PIN to verify
            idempotency_key: Transaction idempotency key

        Returns:
            AuthorizationResult with verification status
        """
        if not pin:
            return AuthorizationResult(verified=False, error="PIN is required", retry_count=0)

        if not is_valid_pin_format(str(pin)):
            return AuthorizationResult(
                verified=False,
                error="Invalid PIN. Enter a 6-digit numeric PIN.",
                retry_count=0,
            )

        if not transaction_type:
            transaction_type = await self.get_transaction_type_from_pending(idempotency_key, phone_number)

        if not transaction_type:
            return AuthorizationResult(
                verified=False,
                error="Transaction session expired. Please start a new transaction.",
                retry_count=0,
            )

        retry_key = f"transaction:retry:{idempotency_key}"
        retry_count = int(await self.redis_client.get(retry_key) or 0)

        if retry_count >= 3:
            return AuthorizationResult(
                verified=False,
                transaction_type=transaction_type,
                error="Maximum PIN attempts exceeded. Please start a new transaction.",
                retry_count=retry_count,
                attempts_remaining=0,
            )

        async with UnitOfWork() as uow:
            if not uow.users:
                return AuthorizationResult(verified=False, error="Database error", retry_count=retry_count)

            user = await uow.users.get_by_phone(phone_number)
            if not user:
                return AuthorizationResult(
                    verified=False,
                    error="User not found. Please contact support.",
                    retry_count=retry_count,
                )

            stored_pin_hash = getattr(user, "transaction_pin", None)
            if not stored_pin_hash:
                return AuthorizationResult(
                    verified=False,
                    error="Transaction PIN not set. Please set up your PIN first.",
                    retry_count=retry_count,
                )

            pin_valid = verify_hash(str(pin), stored_pin_hash)
            user_id = str(user.id)

        if not pin_valid:
            retry_count += 1
            await self.redis_client.set(retry_key, str(retry_count), ex=900)

            attempts_remaining = 3 - retry_count
            error_msg = f"Invalid PIN. {attempts_remaining} attempt(s) remaining."
            if attempts_remaining == 0:
                error_msg = "Invalid PIN. Maximum attempts exceeded. Please start a new transaction."

            return AuthorizationResult(
                verified=False,
                transaction_type=transaction_type,
                error=error_msg,
                retry_count=retry_count,
                attempts_remaining=attempts_remaining,
            )

        return AuthorizationResult(
            verified=True,
            user_id=user_id,
            transaction_type=transaction_type,
            retry_count=retry_count,
            attempts_remaining=3 - retry_count,
        )

    async def store_pin_verification_result(self, idempotency_key: str, result: AuthorizationResult) -> None:
        """
        Store PIN verification result in Redis.

        Args:
            idempotency_key: Transaction idempotency key
            result: AuthorizationResult to store
        """
        pin_verification_key = f"transaction:pin_verified:{idempotency_key}"
        result_data = {
            "verified": result.verified,
            "user_id": result.user_id,
            "transaction_type": result.transaction_type,
            "error": result.error,
            "retry_count": result.retry_count,
            "attempts_remaining": result.attempts_remaining,
        }
        await self.redis_client.setex(pin_verification_key, 900, json.dumps(result_data))

    async def get_pin_verification_result(self, idempotency_key: str) -> AuthorizationResult | None:
        """
        Retrieve PIN verification result from Redis.

        Args:
            idempotency_key: Transaction idempotency key

        Returns:
            AuthorizationResult or None if not found
        """
        pin_verification_key = f"transaction:pin_verified:{idempotency_key}"
        result_data = await self.redis_client.get(pin_verification_key)
        if not result_data:
            return None

        try:
            data = json.loads(result_data)
            return AuthorizationResult(
                verified=data.get("verified", False),
                user_id=data.get("user_id"),
                transaction_type=data.get("transaction_type"),
                error=data.get("error"),
                retry_count=data.get("retry_count", 0),
                attempts_remaining=data.get("attempts_remaining", 0),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    async def claim_pin_resume(self, idempotency_key: str, ttl_seconds: int = 86400) -> bool:
        """Atomically claim a verified PIN resume so queue replays cannot resume twice."""
        claim_key = f"transaction:pin_resume_claim:{idempotency_key}"
        claimed = await self.redis_client.set(claim_key, "1", ex=ttl_seconds, nx=True)
        return bool(claimed)
