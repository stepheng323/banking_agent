"""Mono account authorization provider adapter."""

from typing import Any

from shared.clients.abstractions.account_authorization import (
    AccountAuthorizationProvider,
    ProviderCustomer,
    ProviderMandate,
    ProviderTransferDestination,
)
from shared.clients.providers.mono.client import MonoClient
from shared.config.settings import settings


class MonoAccountAuthorizationProvider(AccountAuthorizationProvider):
    """Mono implementation of account authorization and mandate creation."""

    def __init__(self, client: MonoClient | None = None) -> None:
        self._client = client or MonoClient()

    @property
    def provider_name(self) -> str:
        return "mono"

    @property
    def is_available(self) -> bool:
        return bool(settings.mono_api_key) or settings.use_mono_mock

    async def create_customer(
        self,
        *,
        first_name: str,
        last_name: str,
        phone: str,
        email: str,
        address: str,
        identity_number: str,
        identity_type: str,
    ) -> ProviderCustomer:
        customer = await self._client.create_customer(
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            address=address,
            identity_number=identity_number,
            identity_type=identity_type,
        )
        return ProviderCustomer(id=customer.id, raw_response=_model_dict(customer))

    async def create_mandate(
        self,
        *,
        customer_id: str,
        account_number: str,
        bank_code: str,
        maximum_debit_amount_minor: int,
        reference: str,
        start_date: str,
        end_date: str,
    ) -> ProviderMandate:
        mandate = await self._client.create_mandate(
            customer_id=customer_id,
            account_number=account_number,
            bank_code=bank_code,
            amount=maximum_debit_amount_minor,
            reference=reference,
            start_date=start_date,
            end_date=end_date,
        )
        return ProviderMandate(
            id=mandate.id,
            reference=getattr(mandate, "reference", None),
            status=getattr(mandate, "status", None),
            transfer_destinations=[
                ProviderTransferDestination(bank_name=dest.bank_name, account_number=dest.account_number)
                for dest in mandate.transfer_destinations or []
            ],
            raw_response=_model_dict(mandate),
        )


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, dict) else {}
    return {}
