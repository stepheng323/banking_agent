"""Async validation service to run provider validations in parallel."""

from typing import Any, Dict, Optional, Tuple
import asyncio

from shared.clients.payment.base import PaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AsyncValidationService:
    """Async validation service to run provider validations in parallel."""

    def __init__(self, provider: PaymentProvider) -> None:
        self.provider = provider

    async def validate_account_and_balance(
        self,
        account_number: str,
        bank_code: str,
        source_account_id: str,
        timeout_s: float = 15.0,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Validate recipient account and optionally check source account balance.

        Note: Balance checking is optional and may not be available on all providers.
        If balance check fails, we still return the account resolution result.
        """
        async def _resolve():
            return await self.provider.resolve_account(account_number, bank_code)

        async def _balance():
            try:
                if hasattr(self.provider, 'get_balance'):
                    return await self.provider.get_balance(source_account_id)
            except Exception as e:
                logger.warning("balance_check_unavailable", error=str(e))
            return None

        resolve_task = asyncio.create_task(
            asyncio.wait_for(_resolve(), timeout=timeout_s))
        balance_task = asyncio.create_task(
            asyncio.wait_for(_balance(), timeout=timeout_s))

        resolved, balance = await asyncio.gather(resolve_task, balance_task, return_exceptions=True)

        if isinstance(resolved, Exception):
            exception_type = type(resolved).__name__
            exception_msg = str(resolved) if str(resolved) else f"{exception_type} (no message)"
            
            if isinstance(resolved, asyncio.TimeoutError):
                logger.error(
                    "account_resolution_timeout",
                    timeout_seconds=timeout_s,
                    account_number=account_number,
                    bank_code=bank_code
                )
            else:
                logger.error(
                    "account_resolution_exception",
                    exception_type=exception_type,
                    error=exception_msg,
                    account_number=account_number,
                    bank_code=bank_code,
                    exc_info=resolved
                )
            resolved = None
            
        if isinstance(balance, Exception):
            exception_type = type(balance).__name__
            exception_msg = str(balance) if str(balance) else f"{exception_type} (no message)"
            logger.warning(
                "balance_check_exception",
                exception_type=exception_type,
                error=exception_msg
            )
            balance = None
            
        return resolved, balance
