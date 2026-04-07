"""Planner/router context assembly helpers and size-budget constants."""

import json
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from apps.core.src.agent.graphs.query.session import _session_has_surface_view, is_query_session_stale
from apps.core.src.agent.orchestrator.context.models import ContextFrameType
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CONTEXT_BENEFICIARY_PREVIEW_LIMIT = 5
CONTEXT_ACCOUNT_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_PREVIEW_LIMIT = 5
CONTEXT_HISTORY_ITEM_MAX_CHARS = 150
CONTEXT_USER_STATE_MAX_CHARS = 1200
ROUTER_CONTEXT_MAX_CHARS = 1600
ROUTER_CONTEXT_SECTION_MAX_CHARS = 320
INTERRUPT_CONTEXT_MAX_CHARS = 1800
INTERRUPT_CONTEXT_SECTION_MAX_CHARS = 500
QUOTED_REPLAY_CONTEXT_MAX_CHARS = 1800
QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS = 500
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
    "- If query is waiting for clarification, short answers that complete the missing query detail stay in query "
    "(for example: 'last 3 days', 'today', 'this month').\n"
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
    if isinstance(value, BaseModel):
        return _compact_prompt_value(value.model_dump(mode="json"), depth)

    if is_dataclass(value) and not isinstance(value, type):
        return _compact_prompt_value(asdict(value), depth)

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

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
    if planner_output and getattr(planner_output, "context_read_subtype", None):
        return str(planner_output.context_read_subtype)

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
                    if is_query_session_stale(query_session_snapshot):
                        query_session_snapshot["session_active"] = False
        except Exception:
            query_session_snapshot = None
            query_session_source = None

    if query_session_snapshot is None and isinstance(state.stashed_query_session, dict):
        query_session_snapshot = dict(state.stashed_query_session)
        query_session_source = "stashed"
        if is_query_session_stale(query_session_snapshot):
            query_session_snapshot["session_active"] = False

    snapshot = query_session_snapshot if isinstance(query_session_snapshot, dict) else {}
    logger.info(
        "planner_query_session_snapshot",
        query_session_source=query_session_source or "none",
        session_active=bool(snapshot.get("session_active")),
        has_query_contract=bool(snapshot.get("query_contract")),
        has_query_result=bool(snapshot.get("query_result")),
        has_surface=_session_has_surface_view(snapshot),
        has_query_frames=bool(snapshot.get("query_frames")),
    )

    return query_session_snapshot, query_session_source


def _query_session_summary_text(query_session_snapshot: dict[str, Any] | None) -> tuple[str | None, bool]:
    if not isinstance(query_session_snapshot, dict):
        return None, False
    session_active = bool(query_session_snapshot.get("session_active"))
    summary_text = None
    query_result = query_session_snapshot.get("query_result")
    if isinstance(query_result, dict):
        summary_text = query_result.get("summary_text")
    pending_clarification = query_session_snapshot.get("pending_clarification")
    return (
        _build_query_session_context(
            summary_text if isinstance(summary_text, str) else None,
            pending_clarification if isinstance(pending_clarification, dict) else None,
        ),
        session_active,
    )


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


def summary_to_state_payload(summary: TurnContextSummary) -> dict[str, Any]:
    return asdict(summary)


def summary_from_state_payload(payload: Any) -> TurnContextSummary | None:
    if isinstance(payload, TurnContextSummary):
        return payload
    if isinstance(payload, dict):
        return TurnContextSummary(**payload)
    return None


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

    from apps.core.src.agent.orchestrator.nodes.planner_context_read import _infer_recent_domain_focus
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


def get_or_build_turn_context_summary(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None = None,
    query_session_source: str | None = None,
    path_label: str | None = None,
) -> tuple[TurnContextSummary, dict[str, Any] | None]:
    cached = summary_from_state_payload(state.turn_context_summary)
    if cached is not None:
        return cached, None

    build_start = time.perf_counter()
    summary = build_turn_context_summary(
        state,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
    )
    if path_label:
        from shared.utils.logging import get_logger

        get_logger(__name__).info(
            "perf_timer_latency",
            gate="turn_context_summary_build",
            span="turn_context_summary_build",
            duration_ms=round((time.perf_counter() - build_start) * 1000, 2),
            path_label=path_label,
            phone_number=state.phone_number,
        )
    return summary, {"turn_context_summary": summary_to_state_payload(summary)}


def _build_user_state_summary(state: OrchestratorState) -> str | None:
    """Build a compact, human-readable summary of the user's persistent state."""
    summary, _ = get_or_build_turn_context_summary(state)
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
    include_account_preview: bool = True,
    include_beneficiary_preview: bool = True,
) -> str:
    expected = expected_executors or []
    sections = [
        f"ACTIVE_DOMAIN={summary.active_domain or 'none'}",
        f"SESSION_DOMAIN={summary.session_domain or 'none'}",
        f"RECENT_DOMAIN_FOCUS={summary.recent_domain_focus or 'none'}",
        f"RECENT_ANSWER_FOCUS={summary.recent_answer_focus or 'none'}",
        f"EXPECTED_TRANSACTION_EXECUTORS={','.join(expected) if expected else 'none'}",
    ]

    if include_account_preview and summary.account_lines:
        account_block = ["ACCOUNTS:"] + [f"- {line}" for line in summary.account_lines[:3]]
        if summary.remaining_accounts > 0:
            account_block.append(f"- +{summary.remaining_accounts} more")
        sections.append("\n".join(account_block))

    if include_beneficiary_preview and summary.beneficiary_lines:
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


def build_interrupt_context_from_summary(
    summary: TurnContextSummary,
    *,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    active_task_state_json: str,
    required_fields_json: str,
    prompt_text: str,
    prompt_mode: str = "full",
) -> str:
    sections = [
        (
            "active_flow",
            (
                f"Active Flow: {kind} required for tasks {task_ids} "
                f"(types: {', '.join(sorted(current_task_types)) or 'unknown'}).\n"
                f"active_task_state={active_task_state_json}\n"
                f"required_fields={required_fields_json}\n"
                f"prompt={prompt_text}"
            ),
        )
    ]

    shared_lines = [
        f"RECENT_DOMAIN_FOCUS={summary.recent_domain_focus or 'none'}",
        f"RECENT_ANSWER_FOCUS={summary.recent_answer_focus or 'none'}",
    ]
    if prompt_mode != "compact" and summary.query_session_summary and summary.query_session_active:
        shared_lines.append("QUERY_SESSION:")
        shared_lines.append(_clip_text(summary.query_session_summary, INTERRUPT_CONTEXT_SECTION_MAX_CHARS))
    active_lines: list[str] = []
    if prompt_mode != "compact" and summary.active_flow_summary:
        active_lines.append(summary.active_flow_summary)
    if summary.active_flow_interrupt_kind:
        active_lines.append(f"Interrupt Kind: {summary.active_flow_interrupt_kind}")
    if summary.active_flow_missing_fields:
        active_lines.append(f"Missing Fields: {', '.join(summary.active_flow_missing_fields)}")
    if active_lines:
        shared_lines.append("TURN_CONTEXT_ACTIVE_FLOW:")
        shared_lines.append(
            _clip_text(
                "\n".join(active_lines),
                INTERRUPT_CONTEXT_SECTION_MAX_CHARS,
            )
        )
    sections.append(
        (
            "shared_context",
            _clip_text("\n".join(shared_lines), INTERRUPT_CONTEXT_SECTION_MAX_CHARS),
        )
    )
    context, _, _, _ = _assemble_planner_context(sections, max_chars=INTERRUPT_CONTEXT_MAX_CHARS)
    return context


def build_quoted_replay_context_from_summary(
    summary: TurnContextSummary,
    *,
    quoted_message_id: str | None,
    has_quote: bool,
    quoted_payload_preview: str | None = None,
) -> str:
    header_lines = [
        f"QUOTED_MESSAGE_ID={quoted_message_id or 'unknown'}",
        f"HAS_QUOTE={'true' if has_quote else 'false'}",
        (
            f"QUOTED_ACTIONABLE_PAYLOAD={quoted_payload_preview}"
            if quoted_payload_preview
            else "QUOTED_ACTIONABLE_PAYLOAD=unavailable"
        ),
    ]
    sections = [("quoted_header", "\n".join(header_lines))]

    shared_lines = [
        f"RECENT_DOMAIN_FOCUS={summary.recent_domain_focus or 'none'}",
        f"RECENT_ANSWER_FOCUS={summary.recent_answer_focus or 'none'}",
    ]
    if summary.account_lines:
        shared_lines.append("ACCOUNTS:")
        shared_lines.extend(f"- {line}" for line in summary.account_lines[:2])
    if summary.beneficiary_lines:
        shared_lines.append("BENEFICIARIES:")
        shared_lines.extend(f"- {line}" for line in summary.beneficiary_lines[:2])
    if summary.short_term_memory_summary:
        shared_lines.append("RECENT_CONTEXT:")
        shared_lines.append(_clip_text(summary.short_term_memory_summary, QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS))
    elif summary.history_lines:
        shared_lines.append("RECENT_CHAT:")
        shared_lines.extend(f"- {line}" for line in summary.history_lines[-2:])
    sections.append(
        (
            "shared_context",
            _clip_text("\n".join(shared_lines), QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS),
        )
    )
    context, _, _, _ = _assemble_planner_context(sections, max_chars=QUOTED_REPLAY_CONTEXT_MAX_CHARS)
    return context


def _build_query_session_context(
    summary_text: str | None,
    pending_clarification: dict[str, Any] | None = None,
) -> str:
    summary_snippet = ""
    if summary_text:
        summary_snippet = f' Last summary: "{_clip_text(summary_text, 180)}".'
    clarification_snippet = ""
    if pending_clarification:
        original_query = str(pending_clarification.get("original_query") or "").strip()
        resolver_message = str(pending_clarification.get("resolver_message") or "").strip()
        clarification_parts: list[str] = []
        if original_query:
            clarification_parts.append(f'Unresolved query: "{_clip_text(original_query, 120)}".')
        if resolver_message:
            clarification_parts.append(f'Waiting for: "{_clip_text(resolver_message, 120)}".')
        if clarification_parts:
            clarification_snippet = " " + " ".join(clarification_parts)
    return f"{QUERY_SESSION_CONTEXT_HEADER}{summary_snippet}{clarification_snippet}\n{QUERY_SESSION_CONTEXT_GUIDANCE}"


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
    "INTERRUPT_CONTEXT_MAX_CHARS",
    "PLANNER_CONTEXT_MAX_CHARS",
    "QUOTED_REPLAY_CONTEXT_MAX_CHARS",
    "ROUTER_CONTEXT_MAX_CHARS",
    "TurnContextSummary",
    "_assemble_planner_context",
    "_build_query_session_context",
    "_load_query_session_snapshot",
    "_build_user_state_summary",
    "_clip_text",
    "_compact_payload_for_prompt",
    "build_interrupt_context_from_summary",
    "build_quoted_replay_context_from_summary",
    "build_router_context_from_summary",
    "build_turn_context_summary",
    "build_user_state_summary_from_summary",
    "get_or_build_turn_context_summary",
    "summary_from_state_payload",
    "summary_to_state_payload",
]
