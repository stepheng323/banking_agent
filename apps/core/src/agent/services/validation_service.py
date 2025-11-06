"""Async validation service to run provider validations in parallel."""

from typing import Any, Dict, Optional, Tuple
import asyncio

from shared.clients.payment_provider import PaymentProvider


class AsyncValidationService:
    """Async validation service to run provider validations in parallel."""
    def __init__(self, provider: PaymentProvider) -> None:
        self.provider = provider

    async def validate_account_and_balance(
        self,
        account_number: str,
        bank_code: str,
        source_account_id: str,
        timeout_s: float = 6.0,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Validate recipient account and optionally check source account balance.
        
        Note: Balance checking is optional and may not be available on all providers.
        If balance check fails, we still return the account resolution result.
        """
        async def _resolve():
            return await self.provider.resolve_account(account_number, bank_code)

        async def _balance():
            # Balance check is optional - not all providers support this
            # Return None if method doesn't exist or fails
            try:
                if hasattr(self.provider, 'get_balance'):
                    return await self.provider.get_balance(source_account_id)
            except Exception as e:
                print(f"⚠️  Balance check not available: {e}")
            return None

        resolve_task = asyncio.create_task(
            asyncio.wait_for(_resolve(), timeout=timeout_s))
        balance_task = asyncio.create_task(
            asyncio.wait_for(_balance(), timeout=timeout_s))

        resolved, balance = await asyncio.gather(resolve_task, balance_task, return_exceptions=True)

        if isinstance(resolved, Exception):
            print(f"⚠️  Account resolution exception: {resolved}")
            resolved = None
        if isinstance(balance, Exception):
            print(f"⚠️  Balance check exception (non-critical): {balance}")
            balance = None
        return resolved, balance
