"""Batch funding shortfall formatter."""

from typing import Any

from shared.i18n import render_message


def _naira(amount: float) -> str:
    return f"₦{float(amount):,.0f}"


def format_batch_funding_shortfall(
    shortfalls: list[Any],
    total_demanded: float,
    total_available: float,
    locale: str = "en",
) -> str:
    """Format a deterministic shortfall message for batch funding failures."""
    header = render_message("funding.batch.shortfall_header", locale)
    body = render_message(
        "funding.batch.shortfall_body",
        locale,
        {"demanded": _naira(total_demanded), "available": _naira(total_available)},
    )

    lines: list[str] = [header, "", body, ""]
    for shortfall in shortfalls:
        account_requested = str(
            getattr(shortfall, "account_requested", "") or render_message("funding.format.plan.bank_fallback", locale)
        )
        amount_needed = float(getattr(shortfall, "amount_needed", 0.0))
        account_available = float(getattr(shortfall, "account_available", 0.0))
        deficit = float(getattr(shortfall, "deficit", max(0.0, amount_needed - account_available)))
        task_id = str(getattr(shortfall, "task_id", "task"))

        if account_available > 0:
            lines.append(
                render_message(
                    "funding.batch.task_covered",
                    locale,
                    {"task_id": task_id, "amount": _naira(account_available), "bank": account_requested},
                )
            )
        lines.append(
            render_message(
                "funding.batch.task_short",
                locale,
                {
                    "task_id": task_id,
                    "needed": _naira(amount_needed),
                    "available": _naira(account_available),
                    "bank": account_requested,
                    "deficit": _naira(deficit),
                },
            )
        )

        alternates = getattr(shortfall, "alternate_accounts", None) or []
        if alternates:
            top = alternates[0]
            alt_bank = str(top.get("bank_name") or render_message("funding.format.plan.bank_fallback", locale))
            alt_available = _naira(float(top.get("available", 0.0)))
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
