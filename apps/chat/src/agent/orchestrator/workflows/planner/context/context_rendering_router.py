"""Router context rendering for planner context summaries."""

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_core import (
    ROUTER_CONTEXT_MAX_CHARS,
    ROUTER_CONTEXT_SECTION_MAX_CHARS,
    _clip_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_types import TurnContextSummary


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
            "QUERY_SESSION:\n" + _clip_text(summary.query_session_summary, ROUTER_CONTEXT_SECTION_MAX_CHARS)
        )

    if summary.active_flow_summary:
        active_flow_lines = [summary.active_flow_summary]
        if summary.active_flow_interrupt_kind:
            active_flow_lines.append(f"Interrupt Kind: {summary.active_flow_interrupt_kind}")
        if summary.active_flow_missing_fields:
            active_flow_lines.append(f"Missing Fields: {', '.join(summary.active_flow_missing_fields)}")
        sections.append("ACTIVE_FLOW:\n" + _clip_text("\n".join(active_flow_lines), ROUTER_CONTEXT_SECTION_MAX_CHARS))

    if summary.short_term_memory_summary:
        sections.append(
            "RECENT_CONTEXT:\n" + _clip_text(summary.short_term_memory_summary, ROUTER_CONTEXT_SECTION_MAX_CHARS)
        )
    if summary.referent_memory_summary:
        sections.append(
            "REFERENT_MEMORY:\n" + _clip_text(summary.referent_memory_summary, ROUTER_CONTEXT_SECTION_MAX_CHARS)
        )
    elif not summary.short_term_memory_summary and summary.history_lines:
        sections.append(
            "RECENT_CHAT:\n"
            + _clip_text(
                "\n".join(f"- {line}" for line in summary.history_lines[-3:]),
                ROUTER_CONTEXT_SECTION_MAX_CHARS,
            )
        )

    return _clip_text("\n\n".join(sections), ROUTER_CONTEXT_MAX_CHARS)


__all__ = ["build_router_context_from_summary"]
