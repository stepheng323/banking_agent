"""Planner/router context assembly helpers and size-budget constants."""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from apps.core.src.agent.orchestrator.context.models import ContextFrameType
from apps.core.src.agent.orchestrator.models.state import OrchestratorState

CONTEXT_BENEFICIARY_PREVIEW_LIMIT = 5
CONTEXT_ACCOUNT_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_ITEM_MAX_CHARS = 150
CONTEXT_USER_STATE_MAX_CHARS = 1200
ROUTER_CONTEXT_MAX_CHARS = 1600
ROUTER_CONTEXT_SECTION_MAX_CHARS = 320
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
    "- Data questions are NOT conversational questions. Always route them as query tasks.\n"
    "- Balance/account-status asks are NOT query continuation; route them to account tasks."
)


@dataclass(slots=True)
class TurnContextSummary:
    """Normalized compact state summary for router/planner prompt compilation."""

    active_domain: str | None = None
    session_domain: str | None = None
    recent_domain_focus: str | None = None
    recent_answer_focus: str | None = None
    profile_name: str | None = None
    account_lines: list[str] = field(default_factory=list)
    remaining_accounts: int = 0
    beneficiary_lines: list[str] = field(default_factory=list)
    remaining_beneficiaries: int = 0
    history_lines: list[str] = field(default_factory=list)
    query_session_summary: str | None = None
    query_session_active: bool = False
    query_session_source: str | None = None
    active_flow_summary: str | None = None
    active_flow_intent: str | None = None
    active_flow_missing_fields: list[str] = field(default_factory=list)
    active_flow_interrupt_kind: str | None = None
    short_term_memory_summary: str | None = None


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


def _mask_account_number(value: str) -> str:
    return f"...{value[-4:]}" if len(value) >= 4 else value


def _derive_recent_answer_focus(state: OrchestratorState) -> str | None:
    now = int(time.time())
    for frame in reversed(state.context_frames):
        if (frame.created_at_ts + frame.ttl_seconds) <= now:
            continue
        if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            return "linked_accounts_summary"
        if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
            return "beneficiary_list"
        if frame.frame_type == ContextFrameType.TRANSACTION_LIST:
            return "query_results"
        if frame.frame_type == ContextFrameType.RECEIPT:
            return "receipt"

    planner_output = state.planner_output
    if planner_output and getattr(planner_output, "context_fastpath_subtype", None):
        return str(planner_output.context_fastpath_subtype)

    return None


async def _load_query_session_snapshot(
    state: OrchestratorState,
    redis_client: Any | None,
) -> tuple[dict[str, Any] | None, str | None]:
    query_session_snapshot: dict[str, Any] | None = None
    query_session_source: str | None = None

    if redis_client:
        try:
            query_session_key = f"query:session:{state.phone_number}"
            query_session_data = await redis_client.get(query_session_key)
            if query_session_data:
                if isinstance(query_session_data, bytes):
                    query_session_data = query_session_data.decode("utf-8")
                parsed = json.loads(query_session_data)
                if isinstance(parsed, dict):
                    query_session_snapshot = parsed
                    query_session_source = "redis"
        except Exception:
            query_session_snapshot = None
            query_session_source = None

    if query_session_snapshot is None and isinstance(state.stashed_query_session, dict):
        query_session_snapshot = dict(state.stashed_query_session)
        query_session_source = "stashed"

    return query_session_snapshot, query_session_source


def _query_session_summary_text(query_session_snapshot: dict[str, Any] | None) -> tuple[str | None, bool]:
    if not isinstance(query_session_snapshot, dict):
        return None, False
    session_active = bool(query_session_snapshot.get("session_active"))
    summary_text = None
    query_result = query_session_snapshot.get("query_result")
    if isinstance(query_result, dict):
        summary_text = query_result.get("summary_text")
    return _build_query_session_context(summary_text if isinstance(summary_text, str) else None), session_active


def _build_account_lines(accounts: list[dict[str, Any]]) -> tuple[list[str], int]:
    lines: list[str] = []
    for acc in accounts[:CONTEXT_ACCOUNT_PREVIEW_LIMIT]:
        bank = str(acc.get("bank_name") or "Unknown Bank")
        num = str(acc.get("account_number") or "")
        masked = _mask_account_number(num)
        status = str(acc.get("mandate_status") or "unknown")
        default_tag = " (default)" if acc.get("is_default") else ""
        line = f"{bank} ({masked}) — mandate: {status}{default_tag}"
        if status == "pending":
            extra = acc.get("extra_data", {})
            dests = extra.get("transfer_destinations", []) if isinstance(extra, dict) else []
            if isinstance(dests, list) and dests:
                first_dest = dests[0] if isinstance(dests[0], dict) else {}
                dest_bank = first_dest.get("bank_name")
                dest_num = first_dest.get("account_number")
                if dest_bank and dest_num:
                    line += f" | Activate: ₦50 to {dest_bank} ({dest_num})"
        elif status == "ready":
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


def build_turn_context_summary(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None = None,
    query_session_source: str | None = None,
) -> TurnContextSummary:
    ctx = state.loaded_context or {}
    profile = ctx.get("profile") or {}
    accounts = ctx.get("accounts") or []
    beneficiaries = ctx.get("beneficiaries") or []
    history = ctx.get("history") or []

    profile_name = None
    if isinstance(profile, dict) and profile.get("first_name"):
        profile_name = f"{profile.get('first_name')} {profile.get('last_name') or ''}".strip()

    account_lines, remaining_accounts = _build_account_lines(accounts if isinstance(accounts, list) else [])
    beneficiary_lines, remaining_beneficiaries = _build_beneficiary_lines(
        beneficiaries if isinstance(beneficiaries, list) else []
    )
    history_lines = _build_history_lines(history if isinstance(history, list) else [])
    query_session_summary, query_session_active = _query_session_summary_text(query_session_snapshot)

    active_flow_summary = None
    active_flow_intent = None
    active_flow_missing_fields: list[str] = []
    active_flow_interrupt_kind = state.pending_interrupt.kind if state.pending_interrupt else None
    if state.waves and state.current_wave_index < len(state.waves):
        current_wave = state.waves[state.current_wave_index]
        if current_wave:
            t_id = current_wave[0]
            active_task = state.tasks.get(t_id)
            if active_task:
                active_flow_intent = active_task.type
                payload_view = {
                    k: v for k, v in active_task.payload.items() if k not in ["result", "error", "confirmation"]
                }
                payload_preview = _compact_payload_for_prompt(payload_view)
                active_flow_summary = (
                    f"Active Flow: {active_task.type.upper()} (User is currently in this flow).\n"
                    f"Current Task Data: {payload_preview}\n"
                    f"Routing: slot_updates_keep_intent_unless_user_clearly_switches"
                )
                if state.pending_interrupt and active_task.id in state.pending_interrupt.task_ids:
                    active_flow_missing_fields = list(
                        state.pending_interrupt.fields_by_task.get(active_task.id, []) or []
                    )

    from apps.core.src.agent.orchestrator.nodes.planner_fastpath import _infer_recent_domain_focus
    from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager

    return TurnContextSummary(
        active_domain=state.active_domain,
        session_domain=state.session_stack[-1].domain if state.session_stack else None,
        recent_domain_focus=_infer_recent_domain_focus(state),
        recent_answer_focus=_derive_recent_answer_focus(state),
        profile_name=profile_name,
        account_lines=account_lines,
        remaining_accounts=remaining_accounts,
        beneficiary_lines=beneficiary_lines,
        remaining_beneficiaries=remaining_beneficiaries,
        history_lines=history_lines,
        query_session_summary=query_session_summary,
        query_session_active=query_session_active,
        query_session_source=query_session_source,
        active_flow_summary=active_flow_summary,
        active_flow_intent=active_flow_intent,
        active_flow_missing_fields=active_flow_missing_fields,
        active_flow_interrupt_kind=active_flow_interrupt_kind,
        short_term_memory_summary=OrchestratorContextManager().build_llm_summary(state) or None,
    )


def _build_user_state_summary(state: OrchestratorState) -> str | None:
    """Build a compact, human-readable summary of the user's persistent state."""
    summary = build_turn_context_summary(state)
    return build_user_state_summary_from_summary(summary)


def build_user_state_summary_from_summary(summary: TurnContextSummary) -> str | None:
    if not any(
        [
            summary.profile_name,
            summary.account_lines,
            summary.beneficiary_lines,
            summary.history_lines,
        ]
    ):
        return None

    parts = ["User State:"]

    if summary.profile_name:
        parts.append(f"- Name: {summary.profile_name}")

    if summary.account_lines:
        parts.append("- Accounts:")
        for line in summary.account_lines:
            parts.append(f"  • {line}")
        if summary.remaining_accounts > 0:
            parts.append(f"  • +{summary.remaining_accounts} more account(s)")

    if summary.beneficiary_lines:
        parts.append(f"- Beneficiaries: {len(summary.beneficiary_lines) + summary.remaining_beneficiaries} saved")
        parts.append(f"  • Preview: {', '.join(summary.beneficiary_lines)}")
        if summary.remaining_beneficiaries > 0:
            parts.append(f"  • +{summary.remaining_beneficiaries} more")

    if summary.history_lines:
        parts.append("\nRecent Chat:")
        for line in summary.history_lines:
            parts.append(f"- {line}")

    return _clip_text("\n".join(parts), CONTEXT_USER_STATE_MAX_CHARS)


def build_router_context_from_summary(
    summary: TurnContextSummary,
    *,
    expected_executors: list[str] | None = None,
) -> str:
    expected = expected_executors or []
    sections = [
        f"ACTIVE_DOMAIN={summary.active_domain or 'none'}",
        f"SESSION_DOMAIN={summary.session_domain or 'none'}",
        f"RECENT_DOMAIN_FOCUS={summary.recent_domain_focus or 'none'}",
        f"RECENT_ANSWER_FOCUS={summary.recent_answer_focus or 'none'}",
        f"EXPECTED_TRANSACTION_EXECUTORS={','.join(expected) if expected else 'none'}",
    ]

    if summary.account_lines:
        account_block = ["ACCOUNTS:"] + [f"- {line}" for line in summary.account_lines[:3]]
        if summary.remaining_accounts > 0:
            account_block.append(f"- +{summary.remaining_accounts} more")
        sections.append("\n".join(account_block))

    if summary.beneficiary_lines:
        beneficiary_block = ["BENEFICIARIES:"] + [f"- {line}" for line in summary.beneficiary_lines[:3]]
        if summary.remaining_beneficiaries > 0:
            beneficiary_block.append(f"- +{summary.remaining_beneficiaries} more")
        sections.append("\n".join(beneficiary_block))

    if summary.query_session_summary and summary.query_session_active:
        sections.append(
            "QUERY_SESSION:\n"
            + _clip_text(summary.query_session_summary, ROUTER_CONTEXT_SECTION_MAX_CHARS)
        )

    if summary.active_flow_summary:
        active_flow_lines = [summary.active_flow_summary]
        if summary.active_flow_interrupt_kind:
            active_flow_lines.append(f"Interrupt Kind: {summary.active_flow_interrupt_kind}")
        if summary.active_flow_missing_fields:
            active_flow_lines.append(f"Missing Fields: {', '.join(summary.active_flow_missing_fields)}")
        sections.append(
            "ACTIVE_FLOW:\n"
            + _clip_text("\n".join(active_flow_lines), ROUTER_CONTEXT_SECTION_MAX_CHARS)
        )

    if summary.short_term_memory_summary:
        sections.append(
            "RECENT_CONTEXT:\n" + _clip_text(summary.short_term_memory_summary, ROUTER_CONTEXT_SECTION_MAX_CHARS)
        )
    elif summary.history_lines:
        sections.append(
            "RECENT_CHAT:\n"
            + _clip_text(
                "\n".join(f"- {line}" for line in summary.history_lines[-3:]),
                ROUTER_CONTEXT_SECTION_MAX_CHARS,
            )
        )

    return _clip_text("\n\n".join(sections), ROUTER_CONTEXT_MAX_CHARS)


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
    "ROUTER_CONTEXT_MAX_CHARS",
    "TurnContextSummary",
    "_assemble_planner_context",
    "_build_query_session_context",
    "_load_query_session_snapshot",
    "_build_user_state_summary",
    "_clip_text",
    "_compact_payload_for_prompt",
    "build_router_context_from_summary",
    "build_turn_context_summary",
    "build_user_state_summary_from_summary",
]
