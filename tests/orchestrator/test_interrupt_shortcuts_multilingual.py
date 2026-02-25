"""Unit tests for deterministic multilingual interrupt shortcuts."""

import pytest

from apps.core.src.agent.orchestrator.services.interrupt_shortcuts import (
    resolve_interrupt_shortcut,
    resolve_shortcut_locale,
)
from shared.i18n.models import LocaleCode


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        (LocaleCode.EN, "proceed"),
        (LocaleCode.PCM, "abeg proceed"),
        (LocaleCode.YO, "beeni"),
        (LocaleCode.HA, "na'am"),
        (LocaleCode.IG, "kwe"),
    ],
)
def test_confirmation_approve_shortcuts(locale: LocaleCode, text: str) -> None:
    route = resolve_interrupt_shortcut(text=text, interrupt_kind="confirmation", locale=locale)
    assert route is not None
    assert route.decision == "approve_flow"


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        (LocaleCode.EN, "not now"),
        (LocaleCode.PCM, "no o"),
        (LocaleCode.YO, "rara"),
        (LocaleCode.HA, "a'a"),
        (LocaleCode.IG, "mba"),
    ],
)
def test_confirmation_reject_shortcuts(locale: LocaleCode, text: str) -> None:
    route = resolve_interrupt_shortcut(text=text, interrupt_kind="confirmation", locale=locale)
    assert route is not None
    assert route.decision == "reject_flow"


@pytest.mark.parametrize(
    ("locale", "text", "status_type"),
    [
        (LocaleCode.EN, "where did we stop", "recap"),
        (LocaleCode.EN, "what do you need from me", "requirements"),
        (LocaleCode.PCM, "where we stop", "recap"),
        (LocaleCode.PCM, "wetin remain", "requirements"),
        (LocaleCode.YO, "nibo la duro", "recap"),
        (LocaleCode.YO, "kini mo tun fi ranse", "requirements"),
        (LocaleCode.HA, "ina muka tsaya", "recap"),
        (LocaleCode.HA, "me ya rage", "requirements"),
        (LocaleCode.IG, "ebe anyi kwusiri", "recap"),
        (LocaleCode.IG, "gini foduru", "requirements"),
    ],
)
def test_status_query_shortcuts(locale: LocaleCode, text: str, status_type: str) -> None:
    route = resolve_interrupt_shortcut(text=text, interrupt_kind="input", locale=locale)
    assert route is not None
    assert route.decision == "status_query"
    assert route.status_query_type == status_type


def test_confirmation_guardrails_block_long_or_structured_messages() -> None:
    long_text = "yes " * 20
    structured_text = "change amount to 5000"
    assert resolve_interrupt_shortcut(text=long_text, interrupt_kind="confirmation", locale=LocaleCode.EN) is None
    assert resolve_interrupt_shortcut(text=structured_text, interrupt_kind="confirmation", locale=LocaleCode.EN) is None


def test_auth_yes_text_is_not_confirmation_shortcutted() -> None:
    route = resolve_interrupt_shortcut(text="yes", interrupt_kind="auth", locale=LocaleCode.EN)
    assert route is None


def test_unknown_or_unsupported_locale_falls_back() -> None:
    assert resolve_shortcut_locale("french") is None
    assert resolve_interrupt_shortcut(text="proceed", interrupt_kind="confirmation", locale=None) is None
