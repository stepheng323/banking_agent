"""Native option UI helpers for execution prompts."""

import re
from typing import Any

from shared.utils.logging import get_logger

logger = get_logger(__name__)


def clean_options_prompt(prompt_text: str) -> str:
    """Leave the conversational lead to the UI and render options once.

    Some workers still include numbered candidates in their prompt. Native
    option UIs render those as buttons, while text fallback appends them as a
    numbered list. Removing embedded rows prevents duplicate candidate lists
    without dropping other guidance, such as remaining batch inputs.
    """
    lines: list[str] = []
    for raw_line in prompt_text.splitlines():
        line = raw_line.strip()
        if re.match(r"^\d+[.)]\s+", line):
            continue
        if len(line) >= 2 and line.startswith("_") and line.endswith("_"):
            continue
        if line:
            lines.append(line)
    return "\n".join(lines)


def _compact_beneficiary_button_title(index: int, title: str) -> str:
    compact = re.sub(r"\s+", " ", title).strip()
    if not compact:
        return str(index)
    if not compact.startswith(f"{index}."):
        compact = f"{index}. {compact}"
    return compact[:64]


def _build_show_options_entry(
    *,
    details: dict[str, Any] | None,
    prompt_text: str,
    task_id: str,
    focused_missing_fields: list[str],
) -> dict[str, Any] | None:
    if not isinstance(details, dict):
        return None

    options: list[dict[str, str]] = []
    raw_options = details.get("options")
    if isinstance(raw_options, list):
        for idx, option in enumerate(raw_options, start=1):
            if not isinstance(option, dict):
                continue
            option_id = str(option.get("id", "")).strip() or str(idx)
            title = option.get("display_title") or option.get("title") or option.get("label") or f"Option {idx}"
            rendered = {"id": option_id, "title": str(title)}
            if option.get("button_title"):
                rendered["button_title"] = str(option["button_title"])
            options.append(rendered)

    if not options and "beneficiary_id" in focused_missing_fields:
        raw_candidates = details.get("candidates")
        if isinstance(raw_candidates, list):
            for idx, candidate in enumerate(raw_candidates, start=1):
                if not isinstance(candidate, dict):
                    continue
                option_id = (
                    str(candidate.get("option_id", "")).strip()
                    or str(candidate.get("beneficiary_id", "")).strip()
                    or str(candidate.get("id", "")).strip()
                    or str(idx)
                )
                label = candidate.get("label") or candidate.get("title") or f"Option {idx}"
                title = str(label)
                options.append(
                    {
                        "id": option_id,
                        "title": title,
                        "button_title": _compact_beneficiary_button_title(idx, title),
                    }
                )

    if not options:
        return None

    logger.info("option_prompt_emitted", task_id=task_id, option_count=len(options))
    return {
        "type": "show_options",
        "title": prompt_text,
        "task_ids": [task_id],
        "options": options,
    }


def _compact_prompt_for_options(prompt_text: str) -> str:
    """Strip duplicated numbered option lines when native option UI is available."""
    return prompt_text


__all__ = ["_build_show_options_entry", "_compact_prompt_for_options", "clean_options_prompt"]
