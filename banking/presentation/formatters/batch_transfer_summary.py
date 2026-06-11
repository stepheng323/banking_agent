"""Batch transfer confirmation summary formatting."""

from __future__ import annotations

from typing import Any

from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.i18n.renderer import render_message


def format_batch_transfer_summary(
    num_transfers: int,
    total_amount: Any,
    source_account_info: str | None,
    summaries: list[str],
    locale: str = "en",
    funding_info: str | None = None,
) -> str:
    """Format a confirmation summary for a batch of transfers."""
    title = render_message("transaction_summary.batch.confirm_title", locale, {"count": num_transfers})
    total_str = render_message(
        "transaction_summary.batch.total",
        locale,
        {"amount": format_amount_compact(total_amount)},
    )

    parts = [title]
    if source_account_info:
        parts.append(source_account_info)
    parts.append(total_str)
    if funding_info:
        parts.append("")
        parts.append(funding_info)
    parts.append("")
    parts.append("\n\n".join(summaries))

    return "\n".join(parts)
