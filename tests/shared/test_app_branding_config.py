from __future__ import annotations

from shared.assistant_profile.loader import load_assistant_profile
from shared.config.settings import settings
from shared.i18n import render_message


def test_render_message_uses_app_brand_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Aurora Pay")
    monkeypatch.setattr(settings, "app_name_short", "Aurora")
    monkeypatch.setattr(settings, "app_creator", "Aurora team")
    monkeypatch.setattr(settings, "app_brand_inspiration", "a better myth")
    monkeypatch.setattr(settings, "app_public_base_url", "https://aurora.example")

    greeting = render_message("conversational.greeting", "en")
    brand_origin = render_message("conversational.brand_origin", "en")

    assert greeting == (
        "Hi, I'm Aurora Pay. I can help with transfers, airtime/data, balances, and transactions. "
        "What would you like to do?"
    )
    assert brand_origin == "Aurora Pay is inspired by a better myth."


def test_assistant_profile_loader_applies_brand_overrides(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Aurora Pay")
    monkeypatch.setattr(settings, "app_name_short", "Aurora")
    monkeypatch.setattr(settings, "app_creator", "Aurora team")
    monkeypatch.setattr(settings, "app_brand_inspiration", "a better myth")
    monkeypatch.setattr(settings, "app_public_base_url", "https://aurora.example")

    profile = load_assistant_profile("config/assistant_profile.json")

    assert profile.identity.name == "Aurora Pay"
    assert profile.identity.brand_origin == (
        "Aurora Pay is inspired by a better myth."
    )
