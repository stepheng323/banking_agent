"""Tests for shared conversational style formatting helpers."""

from apps.chat.src.agent.orchestrator.presentation.conversational_style import format_out_of_scope_reply
from banking.presentation.i18n.renderer import render_message


def test_format_out_of_scope_reply_includes_single_empathy_sentence() -> None:
    result = format_out_of_scope_reply(
        "en",
        "I understand this is frustrating. I can help with transfers if you want.",
    )

    assert result == "I understand this is frustrating.\n" + render_message("conversational.out_of_scope", "en")


def test_format_out_of_scope_reply_without_empathy_uses_redirect_only() -> None:
    result = format_out_of_scope_reply("en", None)

    assert result == render_message("conversational.out_of_scope", "en")


def test_format_out_of_scope_reply_deduplicates_existing_redirect() -> None:
    redirect = render_message("conversational.out_of_scope", "en")

    result = format_out_of_scope_reply("en", redirect)

    assert result == redirect
