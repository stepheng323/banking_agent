import re


def _normalize_whitespace(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _summary_title_case(value: str | None) -> str:
    normalized = _normalize_whitespace(value)
    if not normalized:
        return ""
    return normalized.title()


def format_recipient_display_label(recipient_name: str | None, resolved_name: str | None) -> str | None:
    """Return alias-first recipient label, with resolved name in parentheses when different."""
    alias = _normalize_whitespace(recipient_name)
    resolved = _normalize_whitespace(resolved_name)

    if alias and resolved and alias.casefold() != resolved.casefold():
        return f"{alias} ({resolved})"
    if alias:
        return alias
    if resolved:
        return resolved
    return None


def format_summary_recipient_display_label(recipient_name: str | None, resolved_name: str | None) -> str | None:
    """Return alias-first display label for summaries with title-case normalization."""
    alias = _summary_title_case(recipient_name)
    resolved = _summary_title_case(resolved_name)
    if alias and resolved and alias.casefold() != resolved.casefold():
        return f"{alias} ({resolved})"
    if alias:
        return alias
    if resolved:
        return resolved
    return None
