"""Mono implementation of AccountResolverProvider."""

from shared.clients.abstractions.resolution import (
    AccountResolutionResult,
    AccountResolverProvider,
    BankListResult,
    BankRecord,
    ResolvedAccount,
)
from shared.clients.providers.mono.client import MonoClient
from shared.config.settings import settings


class MonoResolverProvider(AccountResolverProvider):
    """Account resolution and bank-directory provider backed by Mono."""

    def __init__(self, mono_client: MonoClient | None = None):
        self._client = mono_client or MonoClient()

    @property
    def provider_name(self) -> str:
        return "mono"

    @property
    def is_available(self) -> bool:
        return bool(settings.mono_api_key)

    async def get_banks(self, country: str = "NG") -> BankListResult:
        del country  # Mono currently returns Nigeria banks only.
        try:
            raw_banks = await self._client.get_banks()
            banks: list[BankRecord] = []
            for bank in raw_banks:
                code = str(bank.get("code") or bank.get("bank_code") or "").strip()
                name = str(bank.get("name") or "").strip()
                if code and name:
                    banks.append(BankRecord(code=code, name=name))
            return BankListResult(success=True, banks=banks, provider=self.provider_name)
        except Exception as e:
            return BankListResult(success=False, banks=[], error=str(e), provider=self.provider_name)

    async def resolve_account(self, account_number: str, bank_code: str) -> AccountResolutionResult:
        try:
            lookup = await self._client.lookup_account_number(account_number, bank_code)
            if not lookup:
                return AccountResolutionResult(
                    success=False,
                    error="Account not found",
                    provider=self.provider_name,
                )
            resolved_bank_code = str(getattr(getattr(lookup, "bank", None), "code", "") or bank_code)
            return AccountResolutionResult(
                success=True,
                account=ResolvedAccount(
                    account_name=str(lookup.name),
                    account_number=str(lookup.account_number),
                    bank_code=resolved_bank_code,
                ),
                provider=self.provider_name,
            )
        except Exception as e:
            return AccountResolutionResult(success=False, error=str(e), provider=self.provider_name)
