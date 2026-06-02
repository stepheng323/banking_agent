from shared.clients.factories.providers import ProviderFactory


class _Resolver:
    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def is_available(self) -> bool:
        return True


def test_resolver_factory_routes_by_flow(monkeypatch) -> None:
    def _create(provider_name: str):
        return _Resolver(provider_name)

    monkeypatch.setattr(ProviderFactory, "create_resolver_provider", staticmethod(_create))

    transfer = ProviderFactory.get_resolver_for_flow("transfer")
    beneficiary = ProviderFactory.get_resolver_for_flow("beneficiary")
    bootstrap = ProviderFactory.get_resolver_for_flow("bootstrap")
    payout = ProviderFactory.get_resolver_for_flow("payout")

    assert transfer is not None and transfer.provider_name == "mono"
    assert beneficiary is not None and beneficiary.provider_name == "mono"
    assert bootstrap is not None and bootstrap.provider_name == "mono"
    assert payout is not None and payout.provider_name == "flutterwave"


def test_resolver_factory_uses_configured_provider_names(monkeypatch) -> None:
    def _create(provider_name: str):
        return _Resolver(provider_name)

    monkeypatch.setattr(ProviderFactory, "create_resolver_provider", staticmethod(_create))
    monkeypatch.setattr("shared.clients.factories.providers.settings.transfer_resolver_provider_name", "okra")
    monkeypatch.setattr("shared.clients.factories.providers.settings.beneficiary_resolver_provider_name", "paystack")
    monkeypatch.setattr("shared.clients.factories.providers.settings.bootstrap_resolver_provider_name", "mono")
    monkeypatch.setattr("shared.clients.factories.providers.settings.payout_resolver_provider_name", "flutterwave")

    transfer = ProviderFactory.get_resolver_for_flow("transfer")
    beneficiary = ProviderFactory.get_resolver_for_flow("beneficiary")
    bootstrap = ProviderFactory.get_resolver_for_flow("bootstrap")
    payout = ProviderFactory.get_resolver_for_flow("payout")

    assert transfer is not None and transfer.provider_name == "okra"
    assert beneficiary is not None and beneficiary.provider_name == "paystack"
    assert bootstrap is not None and bootstrap.provider_name == "mono"
    assert payout is not None and payout.provider_name == "flutterwave"


def test_direct_debit_factory_uses_account_provider(monkeypatch) -> None:
    class _DirectDebit:
        @property
        def provider_name(self) -> str:
            return "configured"

    def _create(provider_name: str):
        assert provider_name == "configured"
        return _DirectDebit()

    monkeypatch.setattr(ProviderFactory, "create_direct_debit_provider", staticmethod(_create))
    monkeypatch.setattr("shared.clients.factories.providers.settings.account_provider_name", "configured")

    provider = ProviderFactory.get_direct_debit_provider()

    assert provider is not None
    assert provider.provider_name == "configured"


def test_account_authorization_factory_uses_account_provider(monkeypatch) -> None:
    class _AccountAuthorization:
        @property
        def provider_name(self) -> str:
            return "configured-auth"

        @property
        def is_available(self) -> bool:
            return True

    def _create(provider_name: str):
        assert provider_name == "configured-auth"
        return _AccountAuthorization()

    monkeypatch.setattr(ProviderFactory, "create_account_authorization_provider", staticmethod(_create))
    monkeypatch.setattr("shared.clients.factories.providers.settings.account_provider_name", "configured-auth")

    provider = ProviderFactory.get_account_authorization_provider()

    assert provider is not None
    assert provider.provider_name == "configured-auth"


def test_account_provider_drives_auth_data_and_debit_factories(monkeypatch) -> None:
    class _Provider:
        def __init__(self, provider_name: str) -> None:
            self._provider_name = provider_name

        @property
        def provider_name(self) -> str:
            return self._provider_name

        @property
        def is_available(self) -> bool:
            return True

    seen: list[tuple[str, str]] = []

    def _direct(provider_name: str):
        seen.append(("direct_debit", provider_name))
        return _Provider(provider_name)

    def _auth(provider_name: str):
        seen.append(("account_authorization", provider_name))
        return _Provider(provider_name)

    def _bank_data(provider_name: str):
        seen.append(("bank_data", provider_name))
        return _Provider(provider_name)

    monkeypatch.setattr("shared.clients.factories.providers.settings.account_provider_name", "linked-provider")
    monkeypatch.setattr(ProviderFactory, "create_direct_debit_provider", staticmethod(_direct))
    monkeypatch.setattr(ProviderFactory, "create_account_authorization_provider", staticmethod(_auth))
    monkeypatch.setattr(ProviderFactory, "create_bank_data_provider", staticmethod(_bank_data))

    direct_debit = ProviderFactory.get_direct_debit_provider()
    account_authorization = ProviderFactory.get_account_authorization_provider()
    bank_data = ProviderFactory.get_bank_data_provider()

    assert direct_debit is not None and direct_debit.provider_name == "linked-provider"
    assert account_authorization is not None and account_authorization.provider_name == "linked-provider"
    assert bank_data is not None and bank_data.provider_name == "linked-provider"
    assert seen == [
        ("direct_debit", "linked-provider"),
        ("account_authorization", "linked-provider"),
        ("bank_data", "linked-provider"),
    ]
