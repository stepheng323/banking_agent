"""Batch funding shortfall formatter."""

from typing import Any

from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.i18n.renderer import render_message
from shared.money import MoneyAmount


def format_batch_funding_shortfall(
    shortfalls: list[Any],
    total_demanded: MoneyAmount,
    total_available: MoneyAmount,
    locale: str = "en",
) -> str:
    """Format a deterministic shortfall message for batch funding failures."""
    header = render_message("funding.batch.shortfall_header", locale)
    body = render_message(
        "funding.batch.shortfall_body",
        locale,
        {"demanded": format_naira(total_demanded), "available": format_naira(total_available)},
    )

    lines: list[str] = [header, "", body, ""]
    for shortfall in shortfalls:
        account_requested = str(
            getattr(shortfall, "account_requested", "") or render_message("funding.format.plan.bank_fallback", locale)
        )
        amount_needed = coerce_amount(getattr(shortfall, "amount_needed", 0))
        account_available = coerce_amount(getattr(shortfall, "account_available", 0))
        deficit = coerce_amount(getattr(shortfall, "deficit", max(coerce_amount(0), amount_needed - account_available)))
        task_id = str(getattr(shortfall, "task_id", "task"))

        if account_available > 0:
            lines.append(
                render_message(
                    "funding.batch.task_covered",
                    locale,
                    {"task_id": task_id, "amount": format_naira(account_available), "bank": account_requested},
                )
            )
        lines.append(
            render_message(
                "funding.batch.task_short",
                locale,
                {
                    "task_id": task_id,
                    "needed": format_naira(amount_needed),
                    "available": format_naira(account_available),
                    "bank": account_requested,
                    "deficit": format_naira(deficit),
                },
            )
        )

        alternates = getattr(shortfall, "alternate_accounts", None) or []
        if alternates:
            top = alternates[0]
            alt_bank = str(top.get("bank_name") or render_message("funding.format.plan.bank_fallback", locale))
            alt_available = format_naira(top.get("available"))
            lines.append(
                render_message(
                    "funding.batch.alternate_suggestion",
                    locale,
                    {"bank": alt_bank, "available": alt_available},
                )
            )
        lines.append("")

    lines.append(render_message("funding.batch.total_infeasible", locale))
    return "\n".join(lines).strip()
