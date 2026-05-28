"""Typed planner context summary shared by context builders and renderers."""

from dataclasses import dataclass, field


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
    referent_memory_summary: str | None = None


__all__ = ["TurnContextSummary"]
