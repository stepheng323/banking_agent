from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.contracts import (
    GateHandler,
    GateHandlerSpec,
    GateLayer,
    GateOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.gate.eligibility import (
    all_of,
    always,
    no_gate_blocking_state,
    no_live_pending_interrupt,
    no_quote,
    phrase_heavy_fastpath_allowed,
    task_planner_available,
)
from apps.chat.src.agent.orchestrator.workflows.gate.engine import ordered_gate_handlers
from apps.chat.src.agent.orchestrator.workflows.gate.family import build_gate_family_handler
from apps.chat.src.agent.orchestrator.workflows.gate.stages.beneficiary_suggestion_stage import (
    _stage_beneficiary_suggestion,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.context_frame_stages import _stage_context_frame_followup
from apps.chat.src.agent.orchestrator.workflows.gate.stages.contextual_followup_stages import (
    _stage_contextual_worker_followup,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.core_stages import (
    _stage_cancel,
    _stage_expired_pin,
    _stage_gibberish_filter,
    _stage_language_switch,
    _stage_stale_interrupt_cleanup,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.data_domain_stages import (
    _stage_data_domain,
    _stage_data_plan_query,
    _stage_data_plan_reference_purchase,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.direct_domain_stages import (
    _stage_account_domain,
    _stage_airtime_domain,
    _stage_balance_direct,
    _stage_beneficiary_domain,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.meta_stages import (
    _stage_banking_ambiguity,
    _stage_deterministic_meta,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.mixed_capability_stages import (
    _stage_mixed_supported_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.query_transfer_stages import (
    _stage_query_and_transfer_domain_guards,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.receipt_stages import (
    _stage_receipt_request,
    _stage_receipt_thread_followup,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.resume_stages import _stage_resume_prompt_action
from apps.chat.src.agent.orchestrator.workflows.gate.stages.schedule_read_stage import _stage_schedule_read_router
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_router_stage import (
    _stage_semantic_router,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_unsupported_capability_stage import (
    _stage_semantic_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.support_context_stages import (
    _stage_support_context_followup,
    _stage_support_issue_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.unsupported_boundary_followup_stage import (
    _stage_capability_boundary_followup,
)


async def _stage_stale_interrupt_cleanup_adapter(ctx: GateContext) -> None:
    _stage_stale_interrupt_cleanup(ctx)


GATE_LEGACY_HANDLER_SPECS: tuple[GateHandlerSpec, ...] = (
    GateHandlerSpec(
        id="stale_interrupt_cleanup",
        layer=GateLayer.PREFLIGHT_CLEANUP,
        priority=10,
        handler=_stage_stale_interrupt_cleanup_adapter,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.CONTINUE_ONLY,
        may_call_llm=False,
        description="Clear dead pending interrupts before any routing stage runs.",
    ),
    GateHandlerSpec(
        id="language_switch",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=10,
        handler=_stage_language_switch,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Handle explicit deterministic locale switch commands.",
    ),
    GateHandlerSpec(
        id="cancel",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=20,
        handler=_stage_cancel,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Handle explicit cancellation and cleanup requests.",
    ),
    GateHandlerSpec(
        id="gibberish_filter",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=30,
        handler=_stage_gibberish_filter,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Reject obvious gibberish before semantic routing.",
    ),
    GateHandlerSpec(
        id="expired_pin",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=40,
        handler=_stage_expired_pin,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Handle stale PIN callbacks after checkpoint cleanup.",
    ),
    GateHandlerSpec(
        id="mixed_supported_unsupported_capability",
        layer=GateLayer.CAPABILITY_GUARDS,
        priority=10,
        handler=_stage_mixed_supported_unsupported_capability,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Split supported banking clauses from unsupported capability clauses.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="capability_boundary_followup",
        layer=GateLayer.CAPABILITY_GUARDS,
        priority=20,
        handler=_stage_capability_boundary_followup,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=True,
        description="Answer short follow-ups to recent unsupported capability refusals.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="semantic_unsupported_capability",
        layer=GateLayer.CAPABILITY_GUARDS,
        priority=30,
        handler=_stage_semantic_unsupported_capability,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.POLICY_BLOCK,
        may_call_llm=True,
        description="Use semantic fallback for unsupported capability detection.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="schedule_read_router",
        layer=GateLayer.SESSION_RESUME,
        priority=10,
        handler=_stage_schedule_read_router,
        owner="semantic_router",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=True,
        description="Route simple scheduled-transaction read requests.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state, task_planner_available),
    ),
    GateHandlerSpec(
        id="resume_prompt_action",
        layer=GateLayer.SESSION_RESUME,
        priority=20,
        handler=_stage_resume_prompt_action,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=True,
        description="Resolve terse replies to live stashed-session resume prompts.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="data_plan_reference_purchase",
        layer=GateLayer.SPECIALIZED_FASTPATHS,
        priority=10,
        handler=_stage_data_plan_reference_purchase,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route data-plan purchases that reference a visible plan.",
        eligibility=all_of(no_live_pending_interrupt, no_quote),
    ),
    GateHandlerSpec(
        id="data_plan_query",
        layer=GateLayer.SPECIALIZED_FASTPATHS,
        priority=20,
        handler=_stage_data_plan_query,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic data-plan catalog lookup requests.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="context_frame_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=10,
        handler=_stage_context_frame_followup,
        owner="semantic_router",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=True,
        description="Ground follow-ups against displayed context frames.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state, task_planner_available),
    ),
    GateHandlerSpec(
        id="receipt_thread_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=20,
        handler=_stage_receipt_thread_followup,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route receipt follow-ups from an active receipt thread.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="support_context_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=30,
        handler=_stage_support_context_followup,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route follow-ups using recent support context.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="receipt_request",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=40,
        handler=_stage_receipt_request,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route recent batch receipt requests.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="beneficiary_suggestion",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=50,
        handler=_stage_beneficiary_suggestion,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Resolve replies to post-transaction beneficiary suggestions.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="contextual_worker_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=60,
        handler=_stage_contextual_worker_followup,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=True,
        description="Answer non-actionable acknowledgements after prior results.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="banking_ambiguity",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=10,
        handler=_stage_banking_ambiguity,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Clarify coded banking phrases that are deterministically ambiguous.",
    ),
    GateHandlerSpec(
        id="support_issue_request",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=20,
        handler=_stage_support_issue_request,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.HINT_ONLY,
        may_call_llm=False,
        description="Attach support routing hints without short-circuiting.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, task_planner_available),
    ),
    GateHandlerSpec(
        id="balance_direct",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=30,
        handler=_stage_balance_direct,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic balance requests.",
        eligibility=all_of(no_live_pending_interrupt, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="account_domain",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=40,
        handler=_stage_account_domain,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic account-domain requests.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="beneficiary_domain",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=50,
        handler=_stage_beneficiary_domain,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic beneficiary-domain requests.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="airtime_domain",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=60,
        handler=_stage_airtime_domain,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic airtime purchase requests.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="data_domain",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=70,
        handler=_stage_data_domain,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic data purchase requests.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="deterministic_meta",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=80,
        handler=_stage_deterministic_meta,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Answer deterministic meta turns such as greetings and identity.",
        eligibility=all_of(no_live_pending_interrupt, no_quote),
    ),
    GateHandlerSpec(
        id="query_and_transfer_domain_guards",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=90,
        handler=_stage_query_and_transfer_domain_guards,
        owner="guardrail",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Route deterministic query and transfer fast paths.",
    ),
    GateHandlerSpec(
        id="semantic_router",
        layer=GateLayer.SEMANTIC_ROUTING,
        priority=10,
        handler=_stage_semantic_router,
        owner="semantic_router",
        outcome_kind=GateOutcomeKind.TASK_DISPATCH,
        may_call_llm=True,
        description="Run top-level semantic routing before planner fallback.",
        eligibility=all_of(no_quote, task_planner_available),
    ),
)


def _specs_for_layer(layer: GateLayer) -> tuple[GateHandlerSpec, ...]:
    return tuple(spec for spec in ordered_gate_handlers(GATE_LEGACY_HANDLER_SPECS) if spec.layer == layer)


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

_GATE_STAGES: tuple[GateHandler, ...] = tuple(
    spec.handler
    for spec in ordered_gate_handlers(GATE_LEGACY_HANDLER_SPECS)
    if spec.layer != GateLayer.PREFLIGHT_CLEANUP
)

__all__ = [
    "GATE_HANDLER_SPECS",
    "GATE_LEGACY_HANDLER_SPECS",
    "GateContext",
    "_GATE_STAGES",
    "_stage_stale_interrupt_cleanup",
]
