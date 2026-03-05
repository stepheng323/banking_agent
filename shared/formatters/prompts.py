"""Prompts formatting utilities for the orchestrator."""

import re
from typing import cast

from shared.formatters.accounts import format_accounts_list
from shared.formatters.transaction_summary import format_amount
from shared.i18n import MessageKey, render_message

_UNSAFE_RECIPIENT_TOKENS = {
    "send",
    "transfer",
    "pay",
    "recipient",
    "her",
    "him",
    "them",
    "that",
    "it",
    "this",
    "previous",
    "to",
    "for",
    "money",
    "cash",
    "funds",
    "s",
}


def sanitize_recipient_display_name(recipient_name: str | None, locale: str = "en") -> str:
    """Return a safe recipient label for user-facing prompts."""
    fallback = render_message("response.common.recipient_fallback", locale)
    if not recipient_name:
        return fallback

    lowered = recipient_name.strip().lower()
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", lowered)
    tokens = [token for token in re.sub(r"[^a-z0-9]+", " ", lowered).split() if token]
    if not tokens:
        return fallback
    if all(token in _UNSAFE_RECIPIENT_TOKENS for token in tokens):
        return fallback
    return recipient_name


def format_batch_transfer_source_prompt(
    resolved_lines: list[str], total_amount: float, amounts: list[float], accounts: list[dict], locale: str = "en"
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
            return f"{prefix}\n\n" + "\n".join(ask_lines)
        return "\n".join(ask_lines)

    if just_resolved_name is not None:
        if just_resolved_bank:
            return cast(
                str,
                render_message(
                    "orchestrator.execution.single_found_with_bank_need_details",
                    locale,
                    {
                        "resolved_name": just_resolved_name,
                        "resolved_bank": just_resolved_bank,
                        "focused_name": focused_display_name,
                    },
                ),
            )
        if just_resolved_name:
            return cast(
                str,
                render_message(
                    "orchestrator.execution.single_found_need_details",
                    locale,
                    {"resolved_name": just_resolved_name, "focused_name": focused_display_name},
                ),
            )
        return cast(
            str,
            render_message(
                "orchestrator.execution.need_account_details_for",
                locale,
                {"focused_name": focused_display_name},
            ),
        )

    if found_names:
        return cast(
            str,
            render_message(
                "orchestrator.execution.found_many_need_details",
                locale,
                {"found_names": ", ".join(found_names), "focused_name": focused_display_name},
            ),
        )
    return cast(
        str,
        render_message(
            "orchestrator.execution.need_account_details_for",
            locale,
            {"focused_name": focused_display_name},
        ),
    )


def format_missing_details_prompt(
    found_names: list[str],
    missing_prompts: list[str],
    feedback_messages: list[str] | None = None,
    locale: str = "en",
) -> str:
    """Format a prompt for multiple missing details."""
    parts = []

    # 1. Feedback (e.g. "I couldn't find your Access bank account")
    if feedback_messages:
        unique_feedback = []
        for f in feedback_messages:
            if f and f not in unique_feedback:
                unique_feedback.append(f)
        parts.extend(unique_feedback)

    # 2. Confirmation (e.g. "I found Tolu")
    if found_names:
        parts.append(
            render_message(
                "orchestrator.execution.found_names",
                locale,
                {"found_names": ", ".join(found_names)},
            )
        )

    if missing_prompts:
        unique_missing = []
        seen_p = set()
        for p in missing_prompts:
            if p and p not in seen_p:
                unique_missing.append(p)
                seen_p.add(p)

        # Ensure a gap between "I found" and the missing prompts
        joined_missing = "\n".join(unique_missing)
        parts.append(joined_missing)

    if parts:
        return "\n\n".join(parts)
    return cast(str, render_message("orchestrator.execution.need_some_details", locale))


def format_auth_reason(task_type: str, locale: str = "en") -> str:
    """Format the authorization reason based on task type."""
    key_by_task: dict[str, MessageKey] = {
        "transfer": "orchestrator.execution.auth_reason_transfer",
        "airtime": "orchestrator.execution.auth_reason_airtime",
        "data": "orchestrator.execution.auth_reason_data",
    }
    key = key_by_task.get(task_type, "orchestrator.execution.auth_reason_default")
    return cast(str, render_message(key, locale))


def format_source_repair_prompt(
    intents: list[str],
    failed_hint: str,
    accounts: list[dict],
    locale: str = "en",
) -> str:
    """Format a specialized repair prompt when a requested bank is missing."""
    parts = []

    # 1. Action Summary
    count = len(intents)
    parts.append(
        render_message(
            "orchestrator.execution.source_repair_header",
            locale,
            {"count": count, "plural_suffix": "s" if count > 1 else ""},
        )
    )
    for intent in intents:
        parts.append(render_message("orchestrator.execution.source_repair_bullet", locale, {"intent": intent}))

    # 2. The Discrepancy
    parts.append("")
    parts.append(
        render_message(
            "orchestrator.execution.source_repair_discrepancy",
            locale,
            {"failed_hint": failed_hint},
        )
    )

    # 3. Fallback Choices
    parts.append(render_message("orchestrator.execution.source_repair_question", locale))

    # Identify unique banks from linked accounts
    linked_banks = []
    seen_banks = set()
    for acc in accounts:
        bank = acc.get("bank_name")
        if bank and bank not in seen_banks:
            linked_banks.append(bank)
            seen_banks.add(bank)

    # Offer top 2 banks as one-click alternatives
    for i, bank in enumerate(linked_banks[:2]):
        choice_text = render_message("orchestrator.execution.source_repair_use_bank", locale, {"bank": bank})
        if count > 1:
            choice_text = render_message(
                "orchestrator.execution.source_repair_use_bank_for_both",
                locale,
                {"bank": bank},
            )
        parts.append(f"{i + 1}. {choice_text}")

    # Final utility choice
    parts.append(
        f"{len(linked_banks[:2]) + 1}. "
        f"{render_message('orchestrator.execution.source_repair_use_different_accounts', locale)}"
    )

    parts.append("")
    choices = ", ".join(str(i + 1) for i in range(len(linked_banks[:2]) + 1))
    if len(linked_banks[:2]) + 1 > 1:
        choices = f"{choices.rsplit(', ', 1)[0]}, or {choices.rsplit(', ', 1)[1]}"
    parts.append(render_message("orchestrator.execution.source_repair_reply_hint", locale, {"choices": choices}))

    return "\n".join(parts)
