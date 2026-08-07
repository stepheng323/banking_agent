"""Generic missing-detail and source-repair prompt formatting."""

from __future__ import annotations

from banking.presentation.i18n.renderer import render_message


def _dedupe_sections(parts: list[str]) -> str:
    unique: list[str] = []
    for part in parts:
        for section in part.split("\n\n"):
            section = section.strip()
            if section and (not unique or section != unique[-1]):
                unique.append(section)
    return "\n\n".join(unique)


def format_missing_details_prompt(
    found_names: list[str],
    missing_prompts: list[str],
    feedback_messages: list[str] | None = None,
    locale: str = "en",
) -> str:
    """Format a prompt for multiple missing details."""
    parts = []

    if feedback_messages:
        unique_feedback = []
        for feedback in feedback_messages:
            if feedback and feedback not in unique_feedback:
                unique_feedback.append(feedback)
        parts.extend(unique_feedback)

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
        seen_prompts = set()
        for prompt in missing_prompts:
            if prompt and prompt not in seen_prompts:
                unique_missing.append(prompt)
                seen_prompts.add(prompt)

        joined_missing = "\n".join(unique_missing)
        parts.append(joined_missing)

    if parts:
        return _dedupe_sections(parts)
    return render_message("orchestrator.execution.need_some_details", locale)


def format_source_repair_prompt(
    intents: list[str],
    failed_hint: str,
    accounts: list[dict],
    locale: str = "en",
) -> str:
    """Format a specialized repair prompt when a requested bank is missing."""
    parts = []

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

    parts.append("")
    parts.append(
        render_message(
            "orchestrator.execution.source_repair_discrepancy",
            locale,
            {"failed_hint": failed_hint},
        )
    )

    parts.append(render_message("orchestrator.execution.source_repair_question", locale))

    linked_banks = []
    seen_banks = set()
    for account in accounts:
        bank = account.get("bank_name")
        if bank and bank not in seen_banks:
            linked_banks.append(bank)
            seen_banks.add(bank)

    for i, bank in enumerate(linked_banks[:2]):
        choice_text = render_message("orchestrator.execution.source_repair_use_bank", locale, {"bank": bank})
        if count > 1:
            choice_text = render_message(
                "orchestrator.execution.source_repair_use_bank_for_both",
                locale,
                {"bank": bank},
            )
        parts.append(f"{i + 1}. {choice_text}")

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
