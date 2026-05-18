"""Continuation clarification and recovery messages."""

from __future__ import annotations

from apps.chat.src.agent.graphs.query.models import QueryResultItem
from shared.formatters.currency import format_naira
from shared.i18n import render_message


def build_soft_clarification(items: list[QueryResultItem], context: str = "", locale: str = "en") -> str:
    """Build a graceful clarification message without resetting context."""
    if not items:
        return render_message("query.clarify.unsure_rephrase", locale)

    context_suffix = render_message("query.clarify.context_suffix", locale, {"context": context}) if context else ""
    lines = [render_message("query.clarify.which_one", locale, {"context_suffix": context_suffix})]
    lines.append("")
    lines.append(render_message("query.clarify.are_you_referring", locale))

    for i, item in enumerate(items[:5], 1):
        amount = format_naira(item.amount, absolute=True) if item.amount else ""
        lines.append(
            render_message(
                "query.clarify.option_line",
                locale,
                {"index": i, "amount": amount, "description": item.description[:30]},
            )
        )

    lines.append("")
    lines.append(render_message("query.clarify.reply_number_or_rephrase", locale))

    return "\n".join(lines)
