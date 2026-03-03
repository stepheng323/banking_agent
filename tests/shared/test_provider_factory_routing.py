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
