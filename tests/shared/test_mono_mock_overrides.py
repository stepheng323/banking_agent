from shared.clients.providers.mono.client import MonoClient
from shared.config.settings import Settings, settings


def _set_minimum_production_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("MONO_API_KEY", "test-mono-key")
    monkeypatch.setenv("MONO_WEBHOOK_SECRET", "test-mono-webhook-secret")
    monkeypatch.setenv("FLUTTERWAVE_SECRET_KEY", "test-flw-key")
    monkeypatch.setenv("FLUTTERWAVE_WEBHOOK_SECRET_HASH", "test-flw-webhook-secret")
    monkeypatch.setenv("META_APP_SECRET", "test-meta-secret")
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


def test_settings_uses_dedicated_query_model_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MODEL", "gpt-4.1-mini")

    cfg = Settings()

    assert cfg.query_model == "gpt-4.1-mini"


def test_settings_query_model_defaults_to_planner_model_until_explicitly_configured(monkeypatch) -> None:
    monkeypatch.delenv("QUERY_MODEL", raising=False)
    monkeypatch.setenv("PLANNER_MODEL", "gpt-4o-mini-test")

    cfg = Settings()

    assert cfg.query_model == "gpt-4o-mini-test"


def test_settings_media_models_have_dedicated_defaults(monkeypatch) -> None:
    monkeypatch.delenv("MEDIA_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("AUDIO_TRANSCRIPTION_MODEL", raising=False)
    monkeypatch.setenv("EXTRACTOR_MODEL", "extractor-test")

    cfg = Settings()

    assert cfg.extractor_model == "extractor-test"
    assert cfg.media_image_model == "gpt-5-mini"
    assert cfg.audio_transcription_model == "gpt-4o-mini-transcribe"


def test_settings_media_models_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("MEDIA_IMAGE_MODEL", "vision-override")
    monkeypatch.setenv("AUDIO_TRANSCRIPTION_MODEL", "audio-override")

    cfg = Settings()

    assert cfg.media_image_model == "vision-override"
    assert cfg.audio_transcription_model == "audio-override"


def test_settings_mono_use_mock_override_takes_precedence(monkeypatch) -> None:
    _set_minimum_production_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONO_USE_MOCK", "true")

    cfg = Settings()

    assert cfg.use_mono_mock is True
    assert cfg.selected_direct_debit_provider == "mock"


def test_mono_client_uses_explicit_mock_toggle(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()

    assert client.use_mock is True


def test_direct_debit_provider_is_only_backward_compatible_fallback(monkeypatch) -> None:
    monkeypatch.delenv("MONO_USE_MOCK", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    cfg = Settings()

    assert cfg.use_mono_mock is True
    assert cfg.selected_direct_debit_provider == "mock"
