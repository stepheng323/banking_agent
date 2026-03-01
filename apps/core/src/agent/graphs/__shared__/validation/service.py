"""Async validation service to run provider validations in parallel."""

import asyncio
from typing import Any

from shared.cache.account_cache import AccountCacheService
from shared.clients.abstractions.payment import PaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AsyncValidationService:
    """Async validation service to run provider validations in parallel."""

    def __init__(self, provider: PaymentProvider) -> None:
        self.provider = provider
        self.account_cache = AccountCacheService()

    async def validate_account(self, bank_code: str, account_number: str) -> dict[str, Any] | None:
        """Resolve account name only."""
        try:
            return await self.account_cache.get_or_fetch(
                account_number,
                bank_code,
                lambda: self.provider.resolve_account(account_number, bank_code),
            )
        except Exception as e:
            logger.error(
                "account_resolution_error",
                error=str(e),
                account=account_number,
                bank=bank_code,
            )
            return None

    async def validate_account_and_balance(
        self,
        account_number: str,
        bank_code: str,
        source_account_id: str,
        timeout_s: float = 15.0,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """
        Validate recipient account and optionally check source account balance.

        Note: Balance checking is optional and may not be available on all providers.
        If balance check fails, we still return the account resolution result.
        """

        async def _resolve() -> dict[str, Any] | None:
            return await self.account_cache.get_or_fetch(
                account_number, bank_code, lambda: self.provider.resolve_account(account_number, bank_code)
            )

        async def _balance() -> dict[str, Any] | None:
            try:
                if hasattr(self.provider, "get_balance"):
                    return await self.provider.get_balance(source_account_id)
            except Exception as e:
                logger.warning("balance_check_unavailable", error=str(e))
            return None

        resolve_task = asyncio.create_task(asyncio.wait_for(_resolve(), timeout=timeout_s))
        balance_task = asyncio.create_task(asyncio.wait_for(_balance(), timeout=timeout_s))

        results = await asyncio.gather(resolve_task, balance_task, return_exceptions=True)

        resolved: dict[str, Any] | None = None
        balance: dict[str, Any] | None = None

        raw_resolved = results[0]
        raw_balance = results[1]

        if isinstance(raw_resolved, Exception):
            exception_type = type(raw_resolved).__name__
            exception_msg = str(raw_resolved) if str(raw_resolved) else f"{exception_type} (no message)"

            if isinstance(raw_resolved, asyncio.TimeoutError):
                logger.error(
                    "account_resolution_timeout",
                    timeout_seconds=timeout_s,
                    account_number=account_number,
                    bank_code=bank_code,
                )
            else:
                logger.error(
                    "account_resolution_exception",
                    exception_type=exception_type,
                    error=exception_msg,
                    account_number=account_number,
                    bank_code=bank_code,
                    exc_info=raw_resolved,
                )
        else:
            resolved = raw_resolved

        if isinstance(raw_balance, Exception):
            exception_type = type(raw_balance).__name__
            exception_msg = str(raw_balance) if str(raw_balance) else f"{exception_type} (no message)"
            logger.warning("balance_check_exception", exception_type=exception_type, error=exception_msg)
        else:
            balance = raw_balance

        return resolved, balance
