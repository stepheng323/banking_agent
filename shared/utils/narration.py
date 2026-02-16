"""Narration formatting utilities."""


def format_narration(raw_narration: str | None, recipient_name: str | None) -> str:
    """Format extracted narration with deterministic rules.

    Args:
        raw_narration: Extracted narration from LLM (e.g. "for food") or None
        recipient_name: Recipient's name for fallback generation (e.g. "Tolu")

    Returns:
        Formatted narration string (never None or empty)
    """
    if not raw_narration:
        if recipient_name:
            return f"Payment to {recipient_name.title()}"
        return "Payment"
    narration = raw_narration.strip()
    prefixes = ["for ", "payment for ", "paying for ", "to pay for "]

    for prefix in prefixes:
        if narration.lower().startswith(prefix):
            narration = narration[len(prefix) :].strip()
            break

    if narration:
        narration = narration[0].upper() + narration[1:]

    if len(narration) > 40:
        narration = narration[:37] + "..."

    return narration or "Payment"
