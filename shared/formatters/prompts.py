"""Prompts formatting utilities for the orchestrator."""

from shared.formatters.accounts import format_accounts_list
from shared.formatters.transaction_summary import format_amount


def format_batch_transfer_source_prompt(
    resolved_lines: list[str], total_amount: float, amounts: list[float], accounts: list[dict]
) -> str:
    """Format the prompt for selecting a source account for a batch transfer."""
    prompt_parts = []

    if resolved_lines:
        prompt_parts.append("\n".join(resolved_lines))

    if total_amount > 0:
        if len(set(amounts)) == 1 and amounts:
            prompt_parts.append(
                f"You're sending {format_amount(amounts[0])} each (total {format_amount(total_amount)})."
            )
        else:
            prompt_parts.append(f"Total: {format_amount(total_amount)}.")

    prompt_parts.append("")

    if accounts:
        prompt_parts.append(format_accounts_list(accounts))
    else:
        prompt_parts.append("*Which account would you like to use?*")

    return "\n".join(prompt_parts)


def format_single_transfer_recipient_prompt(
    focused_name: str, just_resolved_name: str | None, just_resolved_bank: str | None, found_names: list[str]
) -> str:
    """Format the prompt for requesting account details for a single transfer in focus."""
    if just_resolved_name is not None:
        if just_resolved_bank:
            return (
                f"I found {just_resolved_name} ({just_resolved_bank}). I now need account details for {focused_name}."
            )
        elif just_resolved_name:
            return f"I found {just_resolved_name}. I now need account details for {focused_name}."
        else:
            return f"I need account details for {focused_name}."
    else:
        if found_names:
            return f"I found {', '.join(found_names)}. I need account details for {focused_name}."
        else:
            return f"I need account details for {focused_name}."


def format_missing_details_prompt(
    found_names: list[str],
    missing_prompts: list[str],
    feedback_messages: list[str] | None = None,
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
        parts.append(f"I found {', '.join(found_names)}.")

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

    return "\n\n".join(parts) if parts else "I need some details."


def format_auth_reason(task_type: str) -> str:
    """Format the authorization reason based on task type."""
    reasons = {
        "transfer": "Transfer Authorization",
        "airtime": "Airtime Purchase",
        "data": "Data Purchase",
    }
    return reasons.get(task_type, "Authorize Transaction")


def format_source_repair_prompt(
    intents: list[str],
    failed_hint: str,
    accounts: list[dict],
) -> str:
    """Format a specialized repair prompt when a requested bank is missing."""
    parts = []

    # 1. Action Summary
    count = len(intents)
    header = f"Got it — {count} action{'s' if count > 1 else ''}:"
    parts.append(header)
    for intent in intents:
        parts.append(f"• {intent}")

    # 2. The Discrepancy
    parts.append("")
    parts.append(f"You said from your **{failed_hint}**, but I can't see an **{failed_hint}** account linked.")

    # 3. Fallback Choices
    parts.append("Do you want to:")

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
        choice_text = f"Use **{bank}**"
        if count > 1:
            choice_text += " for both"
        parts.append(f"{i + 1}. {choice_text}")

    # Final utility choice
    parts.append(f"{len(linked_banks[:2]) + 1}. Use different accounts for each")

    parts.append("")
    parts.append("_Reply with 1, 2, or 3._")

    return "\n".join(parts)
