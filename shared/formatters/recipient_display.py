def format_recipient_display_label(recipient_name: str | None, resolved_name: str | None) -> str | None:
    """Return alias-first recipient label, with resolved name in parentheses when different."""
    alias = recipient_name.strip() if isinstance(recipient_name, str) else ""
    resolved = resolved_name.strip() if isinstance(resolved_name, str) else ""

    if alias and resolved and alias.casefold() != resolved.casefold():
        return f"{alias} ({resolved})"
    if alias:
        return alias
    if resolved:
        return resolved
    return None
