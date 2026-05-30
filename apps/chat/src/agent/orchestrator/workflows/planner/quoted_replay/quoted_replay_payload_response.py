"""Response helpers for quoted replay shortcuts."""

from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_scope import _scope_requested
from banking.presentation.i18n.renderer import render_message
from shared.types.quoted_replay import QuotedReplayInterpretation


def _quoted_replay_clarify_response(interpretation: QuotedReplayInterpretation, locale: str) -> str:
    if interpretation.decision == "execute" and _scope_requested(interpretation):
        return (
            "I couldn't find a quoted transaction matching that replay request."
            if locale == "en"
            else render_message("conversational.clarify", locale)
        )
    return interpretation.clarify_message or (
        "I can resend that, but I need the missing amount, recipient, or source details first."
        if locale == "en"
        else render_message("conversational.clarify", locale)
    )


__all__ = [
    "_quoted_replay_clarify_response",
]
