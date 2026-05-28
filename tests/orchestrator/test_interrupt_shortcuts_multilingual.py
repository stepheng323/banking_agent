"""Unit tests for deterministic multilingual interrupt shortcuts."""

import pytest

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import (
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
    structured_text = "change beneficiary to tolu"
    assert resolve_interrupt_shortcut(text=long_text, interrupt_kind="confirmation", locale=LocaleCode.EN) is None
    assert resolve_interrupt_shortcut(text=structured_text, interrupt_kind="confirmation", locale=LocaleCode.EN) is None


def test_auth_yes_text_is_not_confirmation_shortcutted() -> None:
    route = resolve_interrupt_shortcut(text="yes", interrupt_kind="auth", locale=LocaleCode.EN)
    assert route is None


@pytest.mark.parametrize("kind", ["input", "confirmation", "auth"])
def test_explicit_cancel_shortcuts_work_for_all_interrupt_kinds(kind: str) -> None:
    route = resolve_interrupt_shortcut(text="abort", interrupt_kind=kind, locale=LocaleCode.EN)
    assert route is not None
    assert route.decision == "cancel"


def test_unknown_or_unsupported_locale_falls_back() -> None:
    assert resolve_shortcut_locale("french") is None
    assert resolve_interrupt_shortcut(text="proceed", interrupt_kind="confirmation", locale=None) is None


def test_confirmation_correction_phrase_falls_through_to_semantic_edit() -> None:
    route = resolve_interrupt_shortcut(
        text="No, I mean split btw them",
        interrupt_kind="confirmation",
        locale=LocaleCode.EN,
    )
    assert route is None


@pytest.mark.parametrize("text", ["make it 20k", "change amount to 5000", "send all", "half"])
def test_confirmation_simple_amount_edits_fall_through_to_semantic_edit(text: str) -> None:
    route = resolve_interrupt_shortcut(
        text=text,
        interrupt_kind="confirmation",
        locale=LocaleCode.EN,
    )
    assert route is None


def test_confirmation_bank_switch_falls_through_to_semantic_edit() -> None:
    route = resolve_interrupt_shortcut(
        text="use first bank instead",
        interrupt_kind="confirmation",
        locale=LocaleCode.EN,
    )
    assert route is None


@pytest.mark.parametrize(
    "text",
    [
        "add that its for march salary",
        "for rent",
        "narration should be school fees",
    ],
)
def test_confirmation_narration_edits_fall_through_to_semantic_edit(text: str) -> None:
    route = resolve_interrupt_shortcut(
        text=text,
        interrupt_kind="confirmation",
        locale=LocaleCode.EN,
    )
    assert route is None


@pytest.mark.parametrize("text", ["repeat that", "show it again", "what do you need again"])
def test_interrupt_restate_shortcuts_route_as_status_queries(text: str) -> None:
    route = resolve_interrupt_shortcut(
        text=text,
        interrupt_kind="confirmation",
        locale=LocaleCode.EN,
    )
    assert route is not None
    assert route.decision == "status_query"


def test_confirmation_correction_without_transfer_cue_falls_back_to_llm() -> None:
    route = resolve_interrupt_shortcut(
        text="No, I mean what is my balance",
        interrupt_kind="confirmation",
        locale=LocaleCode.EN,
    )
    assert route is None
