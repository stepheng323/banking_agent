"""Typed models for planner prompt compilation."""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.types.planner import RouterDomainIntent, TransactionExecutor


@dataclass(frozen=True, slots=True)
class PlannerPromptSignals:
    """State-derived runtime signals used for profile selection."""

    active_flow_type: str | None = None
    pending_interrupt_kind: str | None = None
    query_session_active: bool = False
    query_session_source: str | None = None
    recent_domain_focus: str | None = None
    has_beneficiary_suggestion: bool = False
    has_user_state_summary: bool = False
    has_short_term_memory: bool = False
    has_quote: bool = False
    has_transaction_intent_hint: bool = False
    compact_context: bool = False
    forced_domain_owner: RouterDomainIntent | None = None
    expected_transaction_executors: tuple[TransactionExecutor, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PlannerPromptBuildInput:
    """Input payload for planner prompt compilation."""

    text: str
    context: str
    signals: PlannerPromptSignals


@dataclass(frozen=True, slots=True)
class PlannerPromptBuildResult:
    """Compiled prompt plus observability metadata."""

    system_prompt: str
    profile: str
    selected_rule_ids: tuple[str, ...]
    selected_bundle_ids: tuple[str, ...]
    char_count: int
