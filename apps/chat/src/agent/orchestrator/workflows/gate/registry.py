"""Layered gate family registry."""

from apps.chat.src.agent.orchestrator.workflows.gate.contracts import (
    GateHandlerSpec,
    GateLayer,
    GateOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.gate.eligibility import always
from apps.chat.src.agent.orchestrator.workflows.gate.engine import ordered_gate_handlers
from apps.chat.src.agent.orchestrator.workflows.gate.family import build_gate_family_handler
from apps.chat.src.agent.orchestrator.workflows.gate.stage_specs import GATE_STAGE_SPECS


def _specs_for_layer(layer: GateLayer) -> tuple[GateHandlerSpec, ...]:
    return tuple(spec for spec in ordered_gate_handlers(GATE_STAGE_SPECS) if spec.layer == layer)


def _family_spec(
    *,
    family_id: str,
    layer: GateLayer,
    description: str,
) -> GateHandlerSpec:
    subhandlers = _specs_for_layer(layer)
    return GateHandlerSpec(
        id=family_id,
        layer=layer,
        priority=10,
        handler=build_gate_family_handler(family_id, subhandlers),
        owner="family_router",
        outcome_kind=GateOutcomeKind.FAMILY_ROUTER,
        may_call_llm=any(spec.may_call_llm for spec in subhandlers),
        description=description,
        eligibility=always,
    )


GATE_HANDLER_SPECS: tuple[GateHandlerSpec, ...] = (
    _family_spec(
        family_id="preflight_cleanup_family",
        layer=GateLayer.PREFLIGHT_CLEANUP,
        description="Run preflight cleanup subhandlers before routing.",
    ),
    _family_spec(
        family_id="hard_guardrails_family",
        layer=GateLayer.HARD_GUARDRAILS,
        description="Run deterministic hard guardrail subhandlers.",
    ),
    _family_spec(
        family_id="capability_guards_family",
        layer=GateLayer.CAPABILITY_GUARDS,
        description="Run supported and unsupported capability guard subhandlers.",
    ),
    _family_spec(
        family_id="session_resume_family",
        layer=GateLayer.SESSION_RESUME,
        description="Run session resume and schedule-read subhandlers.",
    ),
    _family_spec(
        family_id="specialized_fastpaths_family",
        layer=GateLayer.SPECIALIZED_FASTPATHS,
        description="Run specialized data-plan fast path subhandlers.",
    ),
    _family_spec(
        family_id="context_followups_family",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        description="Run context follow-up subhandlers.",
    ),
    _family_spec(
        family_id="domain_fastpaths_family",
        layer=GateLayer.DOMAIN_FASTPATHS,
        description="Run deterministic domain fast path subhandlers.",
    ),
    _family_spec(
        family_id="semantic_routing_family",
        layer=GateLayer.SEMANTIC_ROUTING,
        description="Run semantic routing subhandlers before planner fallback.",
    ),
)


__all__ = ["GATE_HANDLER_SPECS"]
