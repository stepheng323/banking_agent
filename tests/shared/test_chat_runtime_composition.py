from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.chat.src.runtime import bundles
from apps.chat.src.runtime import providers as runtime_providers
from apps.chat.src.runtime.repositories import build_chat_runtime_repositories
from banking.persistence.session_scoped import (
    SessionScopedAccountRepository,
    SessionScopedActionableMessageRepository,
    SessionScopedBankTransactionRepository,
    SessionScopedBeneficiaryRepository,
    SessionScopedTransactionRepository,
    SessionScopedUserRepository,
)
from shared.config.settings import settings


def _available_provider(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(is_available=True, **kwargs)


def _patch_available_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_bank_data_provider",
        staticmethod(lambda: _available_provider()),
    )
    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_resolver_for_flow",
        staticmethod(lambda flow: _available_provider(provider_name=flow)),
    )
    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_direct_debit_provider",
        staticmethod(lambda: _available_provider()),
    )
    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_bill_provider",
        staticmethod(lambda: _available_provider()),
    )


def test_provider_bundle_uses_explicit_provider_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    flows: list[str] = []

    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_bank_data_provider",
        staticmethod(lambda: _available_provider(provider_name="bank-data")),
    )

    def _resolver_provider(flow: str) -> SimpleNamespace:
        flows.append(flow)
        return _available_provider(provider_name=f"{flow}-resolver")

    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_resolver_for_flow",
        staticmethod(_resolver_provider),
    )
    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_direct_debit_provider",
        staticmethod(lambda: _available_provider(provider_name="direct-debit")),
    )
    monkeypatch.setattr(
        runtime_providers.ProviderFactory,
        "get_bill_provider",
        staticmethod(lambda: _available_provider(provider_name="bill")),
    )

    bundle = runtime_providers.build_chat_runtime_providers()

    assert bundle.bank_data_provider.provider_name == "bank-data"
    assert bundle.resolver_provider.provider_name == "transfer-resolver"
    assert bundle.payout_resolver_provider.provider_name == "payout-resolver"
    assert bundle.direct_debit_provider.provider_name == "direct-debit"
    assert bundle.bill_provider.provider_name == "bill"
    assert flows == ["transfer", "payout"]


def test_provider_bundle_fails_when_bank_data_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_available_providers(monkeypatch)
    monkeypatch.setattr(runtime_providers.ProviderFactory, "get_bank_data_provider", staticmethod(lambda: None))

    with pytest.raises(
        RuntimeError,
        match=f"Account provider bank-data capability is not configured: {settings.account_provider_name}",
    ):
        runtime_providers.build_chat_runtime_providers()


def test_provider_bundle_fails_when_payout_resolver_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_available_providers(monkeypatch)

    def _resolver_provider(flow: str) -> SimpleNamespace | None:
        if flow == "payout":
            return None
        return _available_provider(provider_name=f"{flow}-resolver")

    monkeypatch.setattr(runtime_providers.ProviderFactory, "get_resolver_for_flow", staticmethod(_resolver_provider))

    with pytest.raises(
        RuntimeError,
        match=f"Payout resolver provider is not configured: {settings.payout_resolver_provider_name}",
    ):
        runtime_providers.build_chat_runtime_providers()


def test_provider_bundle_fails_when_bill_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_available_providers(monkeypatch)
    monkeypatch.setattr(runtime_providers.ProviderFactory, "get_bill_provider", staticmethod(lambda: None))

    with pytest.raises(RuntimeError, match="Bill provider is not configured"):
        runtime_providers.build_chat_runtime_providers()


def test_repository_bundle_uses_session_scoped_repositories() -> None:
    def _session_factory() -> object:
        return object()

    repositories = build_chat_runtime_repositories(_session_factory)

    assert isinstance(repositories.user, SessionScopedUserRepository)
    assert isinstance(repositories.beneficiary, SessionScopedBeneficiaryRepository)
    assert isinstance(repositories.account, SessionScopedAccountRepository)
    assert isinstance(repositories.actionable_message, SessionScopedActionableMessageRepository)
    assert isinstance(repositories.bank_transaction, SessionScopedBankTransactionRepository)
    assert isinstance(repositories.transaction, SessionScopedTransactionRepository)


def test_runtime_bundle_factory_delegates_to_public_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_bundle = (object(), object(), object())
    queue_publisher = object()
    messaging_clients: dict[str, object] = {}
    shared_redis = object()
    llm = object()
    query_llm = object()
    semantic_router_llm = object()
    interrupt_llm = object()
    extractor_llm = object()
    captured: dict[str, object] = {}

    def _build_bundle(**kwargs: object) -> tuple[object, object, object]:
        captured.update(kwargs)
        return expected_bundle

    monkeypatch.setattr(bundles, "build_orchestrator_runtime_bundle", _build_bundle)

    factory = bundles.build_runtime_bundle_factory(
        queue_publisher=queue_publisher,  # type: ignore[arg-type]
        messaging_clients=messaging_clients,  # type: ignore[arg-type]
        shared_redis=shared_redis,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        query_llm=query_llm,  # type: ignore[arg-type]
        semantic_router_llm=semantic_router_llm,  # type: ignore[arg-type]
        interrupt_llm=interrupt_llm,  # type: ignore[arg-type]
        extractor_llm=extractor_llm,  # type: ignore[arg-type]
    )

    assert factory() is expected_bundle
    assert captured == {
        "queue_publisher": queue_publisher,
        "messaging_clients": messaging_clients,
        "shared_redis": shared_redis,
        "llm": llm,
        "query_llm": query_llm,
        "semantic_router_llm": semantic_router_llm,
        "interrupt_llm": interrupt_llm,
        "extractor_llm": extractor_llm,
    }
