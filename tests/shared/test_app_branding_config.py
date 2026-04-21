from __future__ import annotations

from shared.assistant_profile.loader import load_assistant_profile
from shared.config.settings import settings
from shared.i18n import render_message


def test_render_message_uses_app_brand_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Fuspay")
    monkeypatch.setattr(settings, "app_name_short", "Fuspay")
    monkeypatch.setattr(settings, "app_creator", "Fuse Labs")

    greeting = render_message("conversational.greeting", "en")
    brand_origin = render_message("conversational.brand_origin", "en")

    assert greeting == "Hi. I'm Fuspay. What would you like to do?"
    assert brand_origin == "Fuspay is named after a kindler archetype, built by Fuse Labs for calm, reliable banking execution."


def test_assistant_profile_loader_applies_brand_overrides(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Fuspay")
    monkeypatch.setattr(settings, "app_name_short", "Fuspay")
    monkeypatch.setattr(settings, "app_creator", "Fuse Labs")

    profile = load_assistant_profile("config/assistant_profile.json")

    assert profile.identity.name == "Fuspay"
    assert profile.identity.brand_origin == (
        "Fuspay is named after a kindler archetype and built by Fuse Labs for calm, reliable banking execution."
    )
