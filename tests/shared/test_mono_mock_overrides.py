from shared.clients.factories.direct_debit import DirectDebitProviderFactory
from shared.clients.providers.mono.client import MonoClient
from shared.config.settings import Settings, settings


def _set_minimum_production_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("MONO_API_KEY", "test-mono-key")
    monkeypatch.setenv("FLUTTERWAVE_SECRET_KEY", "test-flw-key")
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-meta-access")
    monkeypatch.setenv("META_VERIFY_TOKEN", "test-meta-verify")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-telegram-bot")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-telegram-secret")


def test_settings_force_mock_provider_in_production(monkeypatch) -> None:
    _set_minimum_production_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONO_USE_MOCK", "true")

    cfg = Settings()

    assert cfg.selected_direct_debit_provider == "mock"
    assert cfg.use_mono_mock is True


def test_settings_force_real_mono_in_development(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MONO_USE_MOCK", "false")

    cfg = Settings()

    assert cfg.selected_direct_debit_provider == "mono"
    assert cfg.use_mono_mock is False


def test_settings_mono_use_mock_override_takes_precedence(monkeypatch) -> None:
    _set_minimum_production_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONO_USE_MOCK", "true")

    cfg = Settings()

    assert cfg.use_mono_mock is True
    assert cfg.selected_direct_debit_provider == "mock"


def test_direct_debit_provider_factory_uses_mono_mock_toggle(monkeypatch) -> None:
    DirectDebitProviderFactory.clear_cache()
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings, "app_env", "production")

    provider = DirectDebitProviderFactory.get_provider()

    assert provider.provider_name == "mock"

    DirectDebitProviderFactory.clear_cache()


def test_mono_client_uses_explicit_mock_toggle(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings, "app_env", "production")

    client = MonoClient()

    assert client.use_mock is True


def test_direct_debit_provider_is_only_backward_compatible_fallback(monkeypatch) -> None:
    monkeypatch.delenv("MONO_USE_MOCK", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    cfg = Settings()

    assert cfg.use_mono_mock is True
    assert cfg.selected_direct_debit_provider == "mock"
