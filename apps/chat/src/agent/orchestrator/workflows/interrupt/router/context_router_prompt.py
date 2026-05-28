"""Prompt sizing helpers for interrupt router context."""


def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 16:
        return value[:max_chars]
    return value[: max_chars - 15].rstrip() + " ...[truncated]"


def _select_interrupt_router_prompt_mode(
    *,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
) -> str:
    if kind == "auth":
        return "compact"
    if len(task_ids) != 1:
        return "full"
    if len(current_task_types) > 1:
        return "full"
    return "compact"


__all__ = [
    "_clip_text",
    "_select_interrupt_router_prompt_mode",
]
