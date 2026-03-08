"""Planner context assembly helpers and size-budget constants."""

import json
from typing import Any

from apps.core.src.agent.orchestrator.models.state import OrchestratorState

CONTEXT_BENEFICIARY_PREVIEW_LIMIT = 5
CONTEXT_ACCOUNT_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_ITEM_MAX_CHARS = 150
CONTEXT_USER_STATE_MAX_CHARS = 1200
PLANNER_CONTEXT_MAX_CHARS = 2800
PLANNER_MIN_SECTION_CHARS = 100
PLANNER_CONTEXT_SECTION_SEPARATOR = "\n\n"
PLANNER_ACTIVE_TASK_DATA_MAX_CHARS = 900
PLANNER_ACTIVE_TASK_MAX_KEYS = 12
PLANNER_ACTIVE_TASK_MAX_ITEMS = 5
PLANNER_ACTIVE_TASK_MAX_DEPTH = 2
PLANNER_ACTIVE_TASK_STRING_MAX_CHARS = 120

QUERY_SESSION_CONTEXT_HEADER = "Active Query Session: The user recently viewed transaction results."
QUERY_SESSION_CONTEXT_GUIDANCE = (
    "- Continuation/refinement/analytics on shown transactions stay in query "
    "(for example: 'more', 'next', 'details', 'receipt', 'any credits?', "
    "'any debit?', 'only debits', 'last month', 'how much did I spend?', 'total spending').\n"
    "- Fresh transaction-history asks are also query tasks.\n"
    "- Data questions are NOT conversational questions. Always route them as query tasks."
)


def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 16:
        return value[:max_chars]
    return value[: max_chars - 15].rstrip() + " ...[truncated]"


def _compact_prompt_value(value: Any, depth: int = 0) -> Any:
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

    serialized = json.dumps(compact_payload, ensure_ascii=True)
    return _clip_text(serialized, PLANNER_ACTIVE_TASK_DATA_MAX_CHARS)


def _build_user_state_summary(state: OrchestratorState) -> str | None:
    """Build a compact, human-readable summary of the user's persistent state."""
    ctx = state.loaded_context or {}
    profile = ctx.get("profile") or {}
    accounts = ctx.get("accounts") or []
    beneficiaries = ctx.get("beneficiaries") or []
    history = ctx.get("history") or []

    if not profile and not accounts and not beneficiaries and not history:
        return None

    parts = ["User State:"]

    if profile.get("first_name"):
        name = f"{profile.get('first_name')} {profile.get('last_name') or ''}".strip()
        parts.append(f"- Name: {name}")

    if accounts:
        total_accounts = len(accounts)
        parts.append("- Accounts:")
        for acc in accounts[:CONTEXT_ACCOUNT_PREVIEW_LIMIT]:
            bank = acc.get("bank_name", "Unknown Bank")
            num = acc.get("account_number", "")
            masked = f"...{num[-4:]}" if len(num) >= 4 else num
            status = acc.get("mandate_status")
            default_tag = " (default)" if acc.get("is_default") else ""

            if status == "pending":
                extra = acc.get("extra_data", {})
                dests = extra.get("transfer_destinations", [])
                dest_str = " or ".join([f"{d.get('bank_name')} ({d.get('account_number')})" for d in dests])
                parts.append(f"  • {bank} ({masked}) — mandate: pending ⚠️")
                if dest_str:
                    parts.append(f"    Activate: ₦50 to {dest_str}")
            elif status == "ready":
                parts.append(f"  • {bank} ({masked}) — mandate: ready ✓{default_tag}")
            else:
                parts.append(f"  • {bank} ({masked}) — mandate: {status}{default_tag}")
        remaining_accounts = total_accounts - min(total_accounts, CONTEXT_ACCOUNT_PREVIEW_LIMIT)
        if remaining_accounts > 0:
            parts.append(f"  • +{remaining_accounts} more account(s)")

    if beneficiaries:
        total_beneficiaries = len(beneficiaries)
        ben_strs = []
        for b in beneficiaries[:CONTEXT_BENEFICIARY_PREVIEW_LIMIT]:
            alias = b.get("alias") or b.get("account_name") or "Unknown"
            bank = b.get("bank_name", "")
            num = b.get("account_number", "")
            masked = f"...{num[-4:]}" if len(num) >= 4 else num
            if bank and masked:
                ben_strs.append(f"{alias} ({bank} {masked})")
            else:
                ben_strs.append(alias)

        parts.append(f"- Beneficiaries: {total_beneficiaries} saved")
        if ben_strs:
            parts.append(f"  • Preview: {', '.join(ben_strs)}")
        remaining = total_beneficiaries - len(ben_strs)
        if remaining > 0:
            parts.append(f"  • +{remaining} more")

    if history:
        parts.append("\nRecent Chat:")
        for msg in history[-CONTEXT_HISTORY_PREVIEW_LIMIT:]:
            role = "User" if msg.get("role") == "user" else "Agent"
            content = msg.get("content", "").replace("\n", "  ")
            if len(content) > CONTEXT_HISTORY_ITEM_MAX_CHARS:
                content = _clip_text(content, CONTEXT_HISTORY_ITEM_MAX_CHARS)
            parts.append(f'- {role}: "{content}"')

    return _clip_text("\n".join(parts), CONTEXT_USER_STATE_MAX_CHARS)


def _build_query_session_context(summary_text: str | None) -> str:
    summary_snippet = ""
    if summary_text:
        summary_snippet = f' Last summary: "{_clip_text(summary_text, 180)}".'
    return f"{QUERY_SESSION_CONTEXT_HEADER}{summary_snippet}\n{QUERY_SESSION_CONTEXT_GUIDANCE}"


def _assemble_planner_context(
    sections: list[tuple[str, str]],
    *,
    max_chars: int = PLANNER_CONTEXT_MAX_CHARS,
) -> tuple[str, list[str], list[str], list[str]]:
    assembled: list[str] = []
    included: list[str] = []
    clipped: list[str] = []
    dropped: list[str] = []
    current_len = 0

    for name, section in sections:
        section_text = section.strip()
        if not section_text:
            continue

        separator_len = len(PLANNER_CONTEXT_SECTION_SEPARATOR) if assembled else 0
        remaining = max_chars - current_len - separator_len
        if remaining <= 0:
            dropped.append(name)
            continue

        next_text = section_text
        if len(next_text) > remaining:
            if remaining < PLANNER_MIN_SECTION_CHARS:
                dropped.append(name)
                continue
            next_text = _clip_text(next_text, remaining)
            clipped.append(name)

        assembled.append(next_text)
        included.append(name)
        current_len += separator_len + len(next_text)

    if not assembled:
        return "None", included, clipped, dropped
    return PLANNER_CONTEXT_SECTION_SEPARATOR.join(assembled), included, clipped, dropped


__all__ = [
    "CONTEXT_ACCOUNT_PREVIEW_LIMIT",
    "PLANNER_CONTEXT_MAX_CHARS",
    "_assemble_planner_context",
    "_build_query_session_context",
    "_build_user_state_summary",
    "_clip_text",
    "_compact_payload_for_prompt",
]
