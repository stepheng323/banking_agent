"""Transfer missing-input prompt formatting."""

from __future__ import annotations

from shared.formatters.accounts import format_accounts_list
from shared.formatters.recipient_prompt_names import possessive_display_name, sanitize_recipient_display_name
from shared.formatters.transaction_amounts import format_amount
from shared.i18n.renderer import render_message


def format_batch_transfer_source_prompt(
    resolved_lines: list[str],
    total_amount: float,
    amounts: list[float],
    accounts: list[dict],
    locale: str = "en",
) -> str:
    """Format the prompt for selecting a source account for a batch transfer."""
    prompt_parts = []

    if resolved_lines:
        prompt_parts.append("\n".join(resolved_lines))

    if total_amount > 0:
        if len(set(amounts)) == 1 and amounts:
            prompt_parts.append(
                render_message(
                    "orchestrator.execution.batch_each_total",
                    locale,
                    {"amount_each": format_amount(amounts[0]), "total_amount": format_amount(total_amount)},
                )
            )
        else:
            prompt_parts.append(
                render_message(
                    "orchestrator.execution.batch_total_only",
                    locale,
                    {"total_amount": format_amount(total_amount)},
                )
            )

    prompt_parts.append("")

    if accounts:
        prompt_parts.append(format_accounts_list(accounts, locale=locale))
    else:
        prompt_parts.append(render_message("orchestrator.execution.choose_account_fallback", locale))

    return "\n".join(prompt_parts)


def format_single_transfer_recipient_prompt(
    focused_name: str,
    focused_missing_fields: list[str],
    just_resolved_name: str | None,
    just_resolved_bank: str | None,
    found_names: list[str],
    locale: str = "en",
) -> str:
    """Format the prompt for requesting account details for a single transfer in focus."""
    focused_display_name = sanitize_recipient_display_name(focused_name, locale)
    normalized_missing = set(focused_missing_fields)
    has_account = "recipient_account" in normalized_missing
    has_bank = "recipient_bank_name" in normalized_missing
    needs_account_and_bank = has_account and has_bank
    needs_account = has_account and not has_bank
    needs_bank = has_bank and not has_account

    ask_lines: list[str] = []
    if needs_account_and_bank:
        ask_lines = [
            render_message(
                "response.templates.ask_account_number_and_bank",
                locale,
                {"recipient_name": focused_display_name},
            ),
        ]
    elif needs_account:
        ask_lines = [
            render_message("response.templates.ask_account_number", locale, {"recipient_name": focused_display_name})
        ]
    elif needs_bank:
        ask_lines = [render_message("response.templates.ask_bank", locale)]
    else:
        ask_lines = []

    if ask_lines:
        prefix = ""
        if needs_account_and_bank:
            if just_resolved_name:
                prefix = render_message(
                    "orchestrator.execution.found_names",
                    locale,
                    {"found_names": just_resolved_name},
                )
            elif found_names:
                prefix = render_message(
                    "orchestrator.execution.found_names",
                    locale,
                    {"found_names": ", ".join(found_names)},
                )

        elif just_resolved_name:
            prefix = render_message(
                "orchestrator.execution.found_names",
                locale,
                {"found_names": just_resolved_name},
            )
        elif found_names:
            prefix = render_message(
                "orchestrator.execution.found_names",
                locale,
                {"found_names": ", ".join(found_names)},
            )

        if prefix:
            if needs_account_and_bank and locale == "en":
                ask_lines = [
                    f"I still need {possessive_display_name(focused_display_name, locale)} account number and bank."
                ]
            return f"{prefix}\n\n" + "\n".join(ask_lines)
        return "\n".join(ask_lines)

    if just_resolved_name is not None:
        if just_resolved_bank:
            return render_message(
                "orchestrator.execution.single_found_with_bank_need_details",
                locale,
                {
                    "resolved_name": just_resolved_name,
                    "resolved_bank": just_resolved_bank,
                    "focused_name": focused_display_name,
                },
            )
        if just_resolved_name:
            return render_message(
                "orchestrator.execution.single_found_need_details",
                locale,
                {"resolved_name": just_resolved_name, "focused_name": focused_display_name},
            )
        return render_message(
            "orchestrator.execution.need_account_details_for",
            locale,
            {"focused_name": focused_display_name},
        )

    if found_names:
        return render_message(
            "orchestrator.execution.found_many_need_details",
            locale,
            {"found_names": ", ".join(found_names), "focused_name": focused_display_name},
        )
    return render_message(
        "orchestrator.execution.need_account_details_for",
        locale,
        {"focused_name": focused_display_name},
    )
