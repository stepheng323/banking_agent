from __future__ import annotations

from shared.assistant_profile.loader import load_assistant_profile
from shared.branding import brand_name_aliases, brand_template_params, legacy_brand_names, render_brand_template
from shared.config.settings import Settings, settings
from shared.i18n.renderer import render_message


def _expected_brand_origin(app_name: str, app_name_short: str, inspiration: str, symbolism: str) -> str:
    return (
        f"The name {app_name_short} comes from {inspiration}. We chose it because {symbolism}: "
        f"the idea behind {app_name} is to help your money move smoothly, clearly, and under your control."
    )


def test_render_message_uses_app_brand_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Aurora Pay")
    monkeypatch.setattr(settings, "app_name_short", "Aurora")
    monkeypatch.setattr(settings, "app_creator", "Aurora team")
    monkeypatch.setattr(settings, "app_brand_inspiration", "a better myth")
    monkeypatch.setattr(settings, "app_brand_symbolism", "a better money metaphor")
    monkeypatch.setattr(settings, "app_public_base_url", "https://aurora.example")

    greeting = render_message("conversational.greeting", "en")
    brand_origin = render_message("conversational.brand_origin", "en")

    assert greeting == (
        "Hi, I'm Aurora Pay. I can help with transfers, airtime/data, balances, and transactions. "
        "What would you like to do?"
    )
    assert brand_origin == _expected_brand_origin(
        "Aurora Pay",
        "Aurora",
        "a better myth",
        "a better money metaphor",
    )


def test_brand_template_params_are_centralized(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Aurora Pay")
    monkeypatch.setattr(settings, "app_name_short", "Aurora")
    monkeypatch.setattr(settings, "app_name_aliases", ("Auro",))
    monkeypatch.setattr(settings, "app_legacy_names", ("Old Aurora",))
    monkeypatch.setattr(settings, "app_creator", "Aurora team")
    monkeypatch.setattr(settings, "app_brand_inspiration", "a better myth")
    monkeypatch.setattr(settings, "app_brand_symbolism", "a better money metaphor")
    monkeypatch.setattr(settings, "app_public_base_url", "https://aurora.example")

    params = brand_template_params()

    assert params["app_name"] == "Aurora Pay"
    assert params["app_name_short"] == "Aurora"
    assert params["app_initial"] == "A"
    assert params["app_brand_symbolism"] == "a better money metaphor"
    assert params["app_public_base_url"] == "https://aurora.example"
    assert brand_name_aliases() == {"aurora pay", "aurora", "auro"}
    assert legacy_brand_names() == {"old aurora"}
    assert render_brand_template("{app_name_short} -> {app_public_base_url}") == (
        "Aurora -> https://aurora.example"
    )


def test_brand_aliases_load_from_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_NAME_ALIASES", "Aurora, Aurora Bot")
    monkeypatch.setenv("APP_LEGACY_NAMES", "Old Aurora, Legacy Pay")
    monkeypatch.setenv("APP_BRAND_SYMBOLISM", "clear money movement")

    loaded = Settings()

    assert loaded.app_name_aliases == ("Aurora", "Aurora Bot")
    assert loaded.app_legacy_names == ("Old Aurora", "Legacy Pay")
    assert loaded.app_brand_symbolism == "clear money movement"


def test_assistant_profile_loader_applies_brand_overrides(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Aurora Pay")
    monkeypatch.setattr(settings, "app_name_short", "Aurora")
    monkeypatch.setattr(settings, "app_creator", "Aurora team")
    monkeypatch.setattr(settings, "app_brand_inspiration", "a better myth")
    monkeypatch.setattr(settings, "app_brand_symbolism", "a better money metaphor")
    monkeypatch.setattr(settings, "app_public_base_url", "https://aurora.example")

    profile = load_assistant_profile("config/assistant_profile.json")

    assert profile.identity.name == "Aurora Pay"
    assert profile.identity.brand_origin == _expected_brand_origin(
        "Aurora Pay",
        "Aurora",
        "a better myth",
        "a better money metaphor",
    )
