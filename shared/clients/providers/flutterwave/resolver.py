"""Flutterwave implementation of AccountResolverProvider."""

from shared.clients.abstractions.resolution import (
    AccountResolutionResult,
    AccountResolverProvider,
    BankListResult,
    BankRecord,
    ResolvedAccount,
)
from shared.clients.providers.flutterwave.client import FlutterwaveClient

FLUTTERWAVE_TEST_ACCOUNT = "0690000032"
FLUTTERWAVE_TEST_BANK_CODE = "044"  # Access Bank


class FlutterwaveResolverProvider(AccountResolverProvider):
    """Account resolution and bank-directory provider backed by Flutterwave."""

    def __init__(self, client: FlutterwaveClient | None = None):
        self._client = client or FlutterwaveClient()
        self.use_sandbox = self._client.use_sandbox

    @property
    def provider_name(self) -> str:
        return "flutterwave"

    @property
    def is_available(self) -> bool:
        return self._client.is_configured

    async def get_banks(self, country: str = "NG") -> BankListResult:
        result = await self._client.request("GET", f"/v3/banks/{country}")
        if not result.get("success"):
            return BankListResult(
                success=False,
                banks=[],
                error=str(result.get("error") or "Failed to fetch banks"),
                provider=self.provider_name,
            )

        banks: list[BankRecord] = []
        for bank in result.get("data", []):
            code = str(bank.get("code") or bank.get("bank_code") or "").strip()
            name = str(bank.get("name") or "").strip()
            if code and name:
                banks.append(BankRecord(code=code, name=name))
        return BankListResult(success=True, banks=banks, provider=self.provider_name)

    async def resolve_account(self, account_number: str, bank_code: str) -> AccountResolutionResult:
        original_account = account_number
        original_bank = bank_code

        payload_account = account_number
        payload_bank = bank_code

        if self.use_sandbox:
            payload_account = FLUTTERWAVE_TEST_ACCOUNT
            payload_bank = FLUTTERWAVE_TEST_BANK_CODE

        payload = {"account_number": payload_account, "account_bank": payload_bank}
        result = await self._client.request("POST", "/v3/accounts/resolve", payload=payload, max_retries=3)
        if not result.get("success"):
            return AccountResolutionResult(
                success=False,
                error=str(result.get("error") or "Account resolution failed"),
                provider=self.provider_name,
            )

        data = result.get("data", {})
        account_name = str(data.get("account_name") or "").strip()
        if not account_name:
            return AccountResolutionResult(
                success=False,
                error="Account name not found",
                provider=self.provider_name,
            )

        return AccountResolutionResult(
            success=True,
            account=ResolvedAccount(
                account_name=account_name,
                account_number=original_account,
                bank_code=original_bank,
            ),
            provider=self.provider_name,
        )
