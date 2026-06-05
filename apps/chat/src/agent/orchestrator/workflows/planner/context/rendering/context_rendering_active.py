"""Active-flow context rendering for interrupt and quoted replay routes."""

from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_core import (
    INTERRUPT_CONTEXT_MAX_CHARS,
    INTERRUPT_CONTEXT_SECTION_MAX_CHARS,
    QUOTED_REPLAY_CONTEXT_MAX_CHARS,
    QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS,
    _assemble_planner_context,
    _clip_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_types import TurnContextSummary


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
    if summary.referent_memory_summary:
        shared_lines.append("REFERENT_MEMORY:")
        shared_lines.append(_clip_text(summary.referent_memory_summary, QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS))
    elif not summary.short_term_memory_summary and summary.history_lines:
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


__all__ = ["build_interrupt_context_from_summary", "build_quoted_replay_context_from_summary"]
