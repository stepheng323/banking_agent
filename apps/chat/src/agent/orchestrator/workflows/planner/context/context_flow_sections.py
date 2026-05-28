from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_core import (
    _clip_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_user import (
    build_user_state_summary_from_summary,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS = 640
PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS = 700
PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS = 700
PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS = 220
PLANNER_CONTEXT_USER_STATE_MAX_CHARS = 900


@dataclass(frozen=True)
class PlannerContextSections:
    sections: list[tuple[str, str]]
    has_short_term_memory: bool
    has_user_state_summary: bool
    recent_domain_focus: str | None


def build_planner_context_sections(
    *,
    turn_summary: object,
    query_session_source: str | None,
    is_transactional_flow: bool,
    active_intent: str | None,
    compact_transaction_context: bool,
) -> PlannerContextSections:
    sections: list[tuple[str, str]] = []
    has_short_term_memory = False
    has_user_state_summary = False

    query_session_summary = getattr(turn_summary, "query_session_summary", None)
    if query_session_summary and not is_transactional_flow:
        section_name = "query_session" if query_session_source == "redis" else "query_session_stashed"
        sections.append((section_name, _clip_text(query_session_summary, PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS)))
        logger.info("planner_context_injected", context=section_name)

    active_flow_summary = getattr(turn_summary, "active_flow_summary", None)
    if active_flow_summary and active_intent:
        sections.append(("active_flow", _clip_text(active_flow_summary, PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS)))
        logger.info("planner_context_active_flow_injected", intent=active_intent)

    short_term_memory_summary = getattr(turn_summary, "short_term_memory_summary", None)
    if short_term_memory_summary and not compact_transaction_context:
        has_short_term_memory = True
        sections.append(
            ("short_term_memory", _clip_text(short_term_memory_summary, PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS))
        )
        logger.info("planner_context_injected", context="short_term_memory")

    referent_memory_summary = getattr(turn_summary, "referent_memory_summary", None)
    if referent_memory_summary and not compact_transaction_context:
        has_short_term_memory = True
        sections.append(("referent_memory", _clip_text(referent_memory_summary, PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS)))
        logger.info("planner_context_injected", context="referent_memory")

    recent_domain_focus = getattr(turn_summary, "recent_domain_focus", None)
    if recent_domain_focus and not compact_transaction_context:
        sections.append(
            (
                "recent_domain_focus",
                _clip_text(
                    (
                        f"Recent Domain Focus: {recent_domain_focus}\n"
                        "- Referential/underspecified follow-ups should keep this domain.\n"
                        "- Switch only when user clearly asks another domain."
                    ),
                    PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="recent_domain_focus", domain=recent_domain_focus)

    recent_answer_focus = getattr(turn_summary, "recent_answer_focus", None)
    if recent_answer_focus and not compact_transaction_context:
        sections.append(
            (
                "recent_answer_focus",
                _clip_text(
                    (
                        f"Recent Answer Focus: {recent_answer_focus}\n"
                        "- Prefer grounding short referential follow-ups against this recent surface first."
                    ),
                    PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="recent_answer_focus", focus=recent_answer_focus)

    user_state_summary = (
        build_user_state_summary_from_summary(turn_summary)
        if not compact_transaction_context
        else None
    )
    if user_state_summary:
        has_user_state_summary = True
        sections.append(("user_state_history", _clip_text(user_state_summary, PLANNER_CONTEXT_USER_STATE_MAX_CHARS)))
        logger.info("planner_context_injected", context="user_state_history")

    return PlannerContextSections(
        sections=sections,
        has_short_term_memory=has_short_term_memory,
        has_user_state_summary=has_user_state_summary,
        recent_domain_focus=recent_domain_focus,
    )


__all__ = ["PlannerContextSections", "build_planner_context_sections"]
