"""Payload compaction and preview-line helpers for turn context summaries."""

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_core import _clip_text
from banking.accounts.mandate_state import PENDING, READY, effective_mandate_status
from shared.money import naira_to_json
from shared.utils.json import json_dumps_safe

CONTEXT_BENEFICIARY_PREVIEW_LIMIT = 5
CONTEXT_ACCOUNT_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_ITEM_MAX_CHARS = 150
PLANNER_ACTIVE_TASK_DATA_MAX_CHARS = 900
PLANNER_ACTIVE_TASK_MAX_KEYS = 12
PLANNER_ACTIVE_TASK_MAX_ITEMS = 5
PLANNER_ACTIVE_TASK_MAX_DEPTH = 2
PLANNER_ACTIVE_TASK_STRING_MAX_CHARS = 120


def _compact_prompt_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, BaseModel):
        return _compact_prompt_value(value.model_dump(mode="json"), depth)

    if is_dataclass(value) and not isinstance(value, type):
        return _compact_prompt_value(asdict(value), depth)

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        serialized = naira_to_json(value)
        return serialized if serialized is not None else str(value)

    if isinstance(value, str):
        return _clip_text(value, PLANNER_ACTIVE_TASK_STRING_MAX_CHARS)

    if depth >= PLANNER_ACTIVE_TASK_MAX_DEPTH and isinstance(value, (dict, list)):
        return "...[truncated]"

    if isinstance(value, dict):
        compact_dict: dict[str, Any] = {}
        for idx, (key, nested) in enumerate(value.items()):
            if idx >= PLANNER_ACTIVE_TASK_MAX_ITEMS:
                compact_dict["__more_keys__"] = f"+{len(value) - PLANNER_ACTIVE_TASK_MAX_ITEMS} more"
                break
            compact_dict[str(key)] = _compact_prompt_value(nested, depth + 1)
        return compact_dict

    if isinstance(value, list):
        compact_list = [_compact_prompt_value(item, depth + 1) for item in value[:PLANNER_ACTIVE_TASK_MAX_ITEMS]]
        overflow = len(value) - PLANNER_ACTIVE_TASK_MAX_ITEMS
        if overflow > 0:
            compact_list.append(f"... (+{overflow} more)")
        return compact_list

    return value


def _compact_payload_for_prompt(payload: dict[str, Any]) -> str:
    if not payload:
        return "{}"

    compact_payload: dict[str, Any] = {}
    for idx, (key, value) in enumerate(payload.items()):
        if idx >= PLANNER_ACTIVE_TASK_MAX_KEYS:
            compact_payload["__more_keys__"] = f"+{len(payload) - PLANNER_ACTIVE_TASK_MAX_KEYS} more"
            break
        compact_payload[str(key)] = _compact_prompt_value(value)

    serialized = json_dumps_safe(compact_payload, ensure_ascii=True)
    return _clip_text(serialized, PLANNER_ACTIVE_TASK_DATA_MAX_CHARS)


def _mask_account_number(value: str) -> str:
    return f"...{value[-4:]}" if len(value) >= 4 else value


def _build_account_lines(accounts: list[dict[str, Any]]) -> tuple[list[str], int]:
    lines: list[str] = []
    for acc in accounts[:CONTEXT_ACCOUNT_PREVIEW_LIMIT]:
        bank = str(acc.get("bank_name") or "Unknown Bank")
        num = str(acc.get("account_number") or "")
        masked = _mask_account_number(num)
        status = effective_mandate_status(acc) or "unknown"
        default_tag = " (default)" if acc.get("is_default") else ""
        line = f"{bank} ({masked}) — mandate: {status}{default_tag}"
        if status == PENDING:
            extra = acc.get("extra_data", {})
            dests = extra.get("transfer_destinations", []) if isinstance(extra, dict) else []
            if isinstance(dests, list) and dests:
                first_dest = dests[0] if isinstance(dests[0], dict) else {}
                dest_bank = first_dest.get("bank_name")
                dest_num = first_dest.get("account_number")
                if dest_bank and dest_num:
                    line += f" | Activate: ₦50 to {dest_bank} ({dest_num})"
        elif status == READY:
            line = f"{bank} ({masked}) — mandate: ready ✓{default_tag}"
        lines.append(line)
    return lines, max(0, len(accounts) - len(lines))


def _build_beneficiary_lines(beneficiaries: list[dict[str, Any]]) -> tuple[list[str], int]:
    lines: list[str] = []
    for item in beneficiaries[:CONTEXT_BENEFICIARY_PREVIEW_LIMIT]:
        alias = str(item.get("alias") or item.get("account_name") or "Unknown").strip()
        bank = str(item.get("bank_name") or "").strip()
        num = str(item.get("account_number") or "").strip()
        if bank and num:
            lines.append(f"{alias} | {bank} | {_mask_account_number(num)}")
        else:
            lines.append(alias)
    return lines, max(0, len(beneficiaries) - len(lines))


def _build_history_lines(history: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for msg in history[-CONTEXT_HISTORY_PREVIEW_LIMIT:]:
        role = "user" if msg.get("role") == "user" else "agent"
        content = str(msg.get("content") or "").replace("\n", "  ")
        lines.append(f"{role}: {_clip_text(content, CONTEXT_HISTORY_ITEM_MAX_CHARS)}")
    return lines


__all__ = [
    "CONTEXT_ACCOUNT_PREVIEW_LIMIT",
    "_build_account_lines",
    "_build_beneficiary_lines",
    "_build_history_lines",
    "_compact_payload_for_prompt",
]
