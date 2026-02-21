#!/usr/bin/env python3
"""Generate typed i18n message keys from the English catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "shared" / "i18n" / "catalog" / "en.json"
OUTPUT_PATH = ROOT / "shared" / "i18n" / "message_keys.py"


def _flatten_string_leaves(payload: dict[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, value in payload.items():
        composed = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            keys.append(composed)
        elif isinstance(value, dict):
            keys.extend(_flatten_string_leaves(value, composed))
    return keys


def _build_file(keys: list[str]) -> str:
    sorted_keys = sorted(set(keys))
    if not sorted_keys:
        raise ValueError("No string message keys found in en catalog")

    lines: list[str] = [
        '"""Auto-generated i18n message key typing. Do not edit manually."""',
        "",
        "from typing import Literal, TypeAlias, TypeGuard, cast",
        "",
        "MessageKey: TypeAlias = Literal[",
    ]

    for key in sorted_keys:
        lines.append(f'    "{key}",')
    lines.extend(
        [
            "]",
            "",
            "ALL_MESSAGE_KEYS: tuple[MessageKey, ...] = (",
        ]
    )
    for key in sorted_keys:
        lines.append(f'    "{key}",')
    lines.extend(
        [
            ")",
            "",
            "_ALL_MESSAGE_KEYS_SET = frozenset(ALL_MESSAGE_KEYS)",
            "",
            "",
            "def is_message_key(value: str) -> TypeGuard[MessageKey]:",
            '    """Return True when the string is a known i18n key."""',
            "    return value in _ALL_MESSAGE_KEYS_SET",
            "",
            "",
            "def as_message_key(value: str) -> MessageKey:",
            '    """Assert and cast a runtime string to MessageKey."""',
            "    if not is_message_key(value):",
            '        raise ValueError(f\"Unknown i18n message key: {value}\")',
            "    return cast(MessageKey, value)",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    keys = _flatten_string_leaves(payload)
    OUTPUT_PATH.write_text(_build_file(keys), encoding="utf-8")
    print(f"Generated {len(set(keys))} keys -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
