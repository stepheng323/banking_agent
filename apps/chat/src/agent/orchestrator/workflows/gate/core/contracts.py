"""Typed contracts for the layered gate routing engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext

GateUpdates = dict[str, Any]
GateHandler = Callable[[GateContext], Awaitable[GateUpdates | None]]
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


class GateOutcomeKind(str, Enum):
    """Expected outcome shape for gate handler reviewability."""

    CONTINUE_ONLY = "continue_only"
    DIRECT_RESPONSE = "direct_response"
    TASK_DISPATCH = "task_dispatch"
    PLANNER_HANDOFF = "planner_handoff"
    POLICY_BLOCK = "policy_block"
    HINT_ONLY = "hint_only"


@dataclass(frozen=True, slots=True)
class GateHandlerSpec:
    """Declarative metadata for a gate handler."""

    id: str
    layer: GateLayer
    priority: int
    handler: GateHandler
    owner: str
    outcome_kind: GateOutcomeKind
    may_call_llm: bool
    description: str
    eligibility: GateEligibility | None = None


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
    routing_owner: str | None = None
    routing_decision: str | None = None
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
    "GateOutcomeKind",
    "GateTraceEntry",
    "GateUpdates",
]
