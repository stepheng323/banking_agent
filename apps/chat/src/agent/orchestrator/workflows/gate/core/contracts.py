"""Typed contracts for the layered gate routing engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    TurnDirective,
    TurnNextStep,
    TurnOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext

GateUpdates = dict[str, Any]
GateHandler = Callable[[GateContext], Awaitable[RouteResolution | None]]
GateEligibility = Callable[[GateContext], "GateEligibilityResult"]


class GateLayer(str, Enum):
    """High-level ordered layers for gate routing."""

    PREFLIGHT_CLEANUP = "preflight_cleanup"
    HARD_GUARDRAILS = "hard_guardrails"
    CAPABILITY_GUARDS = "capability_guards"
    SESSION_RESUME = "session_resume"
    SPECIALIZED_FASTPATHS = "specialized_fastpaths"
    CONTEXT_FOLLOWUPS = "context_followups"
    DOMAIN_FASTPATHS = "domain_fastpaths"
    SEMANTIC_ROUTING = "semantic_routing"
    PLANNER_FALLBACK = "planner_fallback"


@dataclass(frozen=True, slots=True)
class GateHandlerSpec:
    """Declarative metadata for a gate handler."""

    id: str
    layer: GateLayer
    priority: int
    handler: GateHandler
    owner: str
    outcome_kind: TurnOutcomeKind | None
    may_call_llm: bool
    description: str
    eligibility: GateEligibility | None = None
    allowed_owners: frozenset[str] = frozenset()
    allowed_outcomes: frozenset[TurnOutcomeKind] = frozenset()
    allowed_next_steps: frozenset[TurnNextStep] = frozenset()

    @property
    def resolved_allowed_outcomes(self) -> frozenset[TurnOutcomeKind]:
        if self.allowed_outcomes:
            return self.allowed_outcomes
        return frozenset({self.outcome_kind}) if self.outcome_kind is not None else frozenset()

    @property
    def resolved_allowed_owners(self) -> frozenset[str]:
        return self.allowed_owners or frozenset({self.owner})

    @property
    def resolved_allowed_next_steps(self) -> frozenset[TurnNextStep]:
        if self.allowed_next_steps:
            return self.allowed_next_steps
        steps: set[TurnNextStep] = set()
        for outcome in self.resolved_allowed_outcomes:
            if outcome in {TurnOutcomeKind.DIRECT_RESPONSE, TurnOutcomeKind.POLICY_BLOCK}:
                steps.add(TurnNextStep.FINALIZE)
            elif outcome == TurnOutcomeKind.TASK_DISPATCH:
                steps.add(TurnNextStep.ADVANCE)
            elif outcome == TurnOutcomeKind.PLANNER_HANDOFF:
                steps.add(TurnNextStep.PLAN)
            elif outcome == TurnOutcomeKind.INTERRUPT_HANDOFF:
                steps.add(TurnNextStep.HANDLE_INTERRUPT)
        return frozenset(steps)


@dataclass(frozen=True, slots=True)
class GateEligibilityResult:
    """Pre-handler eligibility result for gate traceability."""

    eligible: bool
    reason: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class GateTraceEntry:
    """Compact per-handler gate trace entry."""

    handler_id: str
    layer: GateLayer
    duration_ms: float
    matched: bool
    executed: bool
    gate_updates_changed: bool
    turn_directive: TurnDirective | None = None
    skip_reason: str | None = None
    skip_details: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class GateEngineResult:
    """Result returned by the layered gate engine."""

    updates: GateUpdates
    matched_handler_id: str
    matched_layer: GateLayer
    trace: tuple[GateTraceEntry, ...]


GATE_LAYER_ORDER: tuple[GateLayer, ...] = (
    GateLayer.PREFLIGHT_CLEANUP,
    GateLayer.HARD_GUARDRAILS,
    GateLayer.CAPABILITY_GUARDS,
    GateLayer.SESSION_RESUME,
    GateLayer.SPECIALIZED_FASTPATHS,
    GateLayer.CONTEXT_FOLLOWUPS,
    GateLayer.DOMAIN_FASTPATHS,
    GateLayer.SEMANTIC_ROUTING,
    GateLayer.PLANNER_FALLBACK,
)

GATE_LAYER_INDEX: dict[GateLayer, int] = {layer: index for index, layer in enumerate(GATE_LAYER_ORDER)}


__all__ = [
    "GATE_LAYER_INDEX",
    "GATE_LAYER_ORDER",
    "GateEngineResult",
    "GateEligibility",
    "GateEligibilityResult",
    "GateHandler",
    "GateHandlerSpec",
    "GateLayer",
    "GateTraceEntry",
    "GateUpdates",
    "TurnOutcomeKind",
]
