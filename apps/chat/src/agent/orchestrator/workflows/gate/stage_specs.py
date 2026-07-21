"""Flat gate stage declarations used by the layered gate registry."""

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.contracts import (
    GateHandlerSpec,
    GateLayer,
    TurnOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import interrupt_handoff
from apps.chat.src.agent.orchestrator.workflows.gate.eligibility import (
    all_of,
    no_gate_blocking_state,
    no_live_pending_interrupt,
    no_quote,
    phrase_heavy_fastpath_allowed,
    task_planner_available,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.capability_boundary_stages import (
    _stage_capability_boundary_followup,
    _stage_deterministic_unsupported_capability,
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
from apps.chat.src.agent.orchestrator.workflows.gate.stages.schedule_read_stage import _stage_schedule_read_router
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_routing import (
    _stage_semantic_router,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.session_action_stages import (
    _stage_beneficiary_suggestion,
    _stage_resume_prompt_action,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.stale_context_arbitration import (
    _stage_stale_context_arbitration,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.support_receipt_stages import (
    _stage_receipt_request,
    _stage_receipt_thread_followup,
    _stage_recent_transaction_support_request,
    _stage_support_context_followup,
    _stage_support_issue_request,
)


async def _stage_stale_interrupt_cleanup_adapter(ctx: GateContext) -> None:
    _stage_stale_interrupt_cleanup(ctx)


async def _stage_pending_interrupt(ctx: GateContext) -> RouteResolution | None:
    """Commit the live interrupt as an explicit gate outcome."""
    pending_interrupt = ctx.gate_updates.get("pending_interrupt", ctx.state_view.pending_interrupt)
    if pending_interrupt is None:
        return None
    return interrupt_handoff(
        ctx,
        pending_interrupt=pending_interrupt,
        decision="interrupt_handoff",
        source="pending_interrupt_stage",
    )


GATE_STAGE_SPECS: tuple[GateHandlerSpec, ...] = (
    GateHandlerSpec(
        id="stale_interrupt_cleanup",
        layer=GateLayer.PREFLIGHT_CLEANUP,
        priority=10,
        handler=_stage_stale_interrupt_cleanup_adapter,
        owner="guardrail",
        outcome_kind=None,
        may_call_llm=False,
        description="Clear dead pending interrupts before any routing stage runs.",
    ),
    GateHandlerSpec(
        id="language_switch",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=10,
        handler=_stage_language_switch,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Handle explicit deterministic locale switch commands.",
    ),
    GateHandlerSpec(
        id="cancel",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=20,
        handler=_stage_cancel,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Handle explicit cancellation and cleanup requests.",
    ),
    GateHandlerSpec(
        id="gibberish_filter",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=30,
        handler=_stage_gibberish_filter,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Reject obvious gibberish before semantic routing.",
    ),
    GateHandlerSpec(
        id="expired_pin",
        layer=GateLayer.HARD_GUARDRAILS,
        priority=40,
        handler=_stage_expired_pin,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Handle stale PIN callbacks after checkpoint cleanup.",
    ),
    GateHandlerSpec(
        id="mixed_supported_unsupported_capability",
        layer=GateLayer.CAPABILITY_GUARDS,
        priority=10,
        handler=_stage_mixed_supported_unsupported_capability,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset(
            {
                TurnOutcomeKind.DIRECT_RESPONSE,
                TurnOutcomeKind.TASK_DISPATCH,
                TurnOutcomeKind.POLICY_BLOCK,
            }
        ),
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
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=True,
        description="Answer short follow-ups to recent unsupported capability refusals.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="deterministic_unsupported_capability",
        layer=GateLayer.CAPABILITY_GUARDS,
        priority=25,
        handler=_stage_deterministic_unsupported_capability,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.POLICY_BLOCK,
        may_call_llm=False,
        description="Deterministic check for unsupported capability boundaries matching registry phrases.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="pending_interrupt",
        layer=GateLayer.SESSION_RESUME,
        priority=1,
        handler=_stage_pending_interrupt,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.INTERRUPT_HANDOFF,
        may_call_llm=False,
        description="Commit a live pending interrupt before session and domain routing.",
    ),
    GateHandlerSpec(
        id="schedule_read_router",
        layer=GateLayer.SESSION_RESUME,
        priority=10,
        handler=_stage_schedule_read_router,
        owner="semantic_router",
        allowed_owners=frozenset({"guardrail", "semantic_router"}),
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
        may_call_llm=False,
        description="Route deterministic data-plan catalog lookup requests.",
        eligibility=all_of(no_live_pending_interrupt, no_quote, phrase_heavy_fastpath_allowed),
    ),
    GateHandlerSpec(
        id="stale_context_arbitration",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=5,
        handler=_stage_stale_context_arbitration,
        owner="semantic_router",
        allowed_owners=frozenset({"guardrail", "query_session", "semantic_router"}),
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset(
            {
                TurnOutcomeKind.DIRECT_RESPONSE,
                TurnOutcomeKind.TASK_DISPATCH,
                TurnOutcomeKind.PLANNER_HANDOFF,
                TurnOutcomeKind.POLICY_BLOCK,
            }
        ),
        may_call_llm=True,
        description="Semantically arbitrate non-terse turns while stale context is active.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state, no_quote, task_planner_available),
    ),
    GateHandlerSpec(
        id="recent_transaction_support_request",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=10,
        handler=_stage_recent_transaction_support_request,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset(
            {
                TurnOutcomeKind.DIRECT_RESPONSE,
                TurnOutcomeKind.TASK_DISPATCH,
                TurnOutcomeKind.POLICY_BLOCK,
            }
        ),
        may_call_llm=False,
        description="Route reversal/refund follow-ups against a just-displayed transaction.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="support_issue_request",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=15,
        handler=_stage_support_issue_request,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
        may_call_llm=False,
        description="Route clear transaction or ticket problem statements to support.",
        eligibility=all_of(no_live_pending_interrupt, no_quote),
    ),
    GateHandlerSpec(
        id="context_frame_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=20,
        handler=_stage_context_frame_followup,
        owner="semantic_router",
        allowed_owners=frozenset({"guardrail", "semantic_router"}),
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.DIRECT_RESPONSE, TurnOutcomeKind.TASK_DISPATCH}),
        may_call_llm=False,
        description="Handle deterministic displayed-frame selectors; semantic follow-ups use the router call.",
        eligibility=all_of(no_live_pending_interrupt, no_gate_blocking_state),
    ),
    GateHandlerSpec(
        id="receipt_thread_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=30,
        handler=_stage_receipt_thread_followup,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
        may_call_llm=False,
        description="Route receipt follow-ups from an active receipt thread.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="support_context_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=40,
        handler=_stage_support_context_followup,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
        may_call_llm=False,
        description="Route follow-ups using recent support context.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="receipt_request",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=50,
        handler=_stage_receipt_request,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
        may_call_llm=False,
        description="Route recent batch receipt requests.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="beneficiary_suggestion",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=60,
        handler=_stage_beneficiary_suggestion,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        may_call_llm=False,
        description="Resolve replies to post-transaction beneficiary suggestions.",
        eligibility=no_live_pending_interrupt,
    ),
    GateHandlerSpec(
        id="contextual_worker_followup",
        layer=GateLayer.CONTEXT_FOLLOWUPS,
        priority=70,
        handler=_stage_contextual_worker_followup,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
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
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        may_call_llm=False,
        description="Clarify coded banking phrases that are deterministically ambiguous.",
    ),
    GateHandlerSpec(
        id="balance_direct",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=30,
        handler=_stage_balance_direct,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
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
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset({TurnOutcomeKind.TASK_DISPATCH, TurnOutcomeKind.POLICY_BLOCK}),
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
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
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
        allowed_owners=frozenset({"guardrail", "query_session"}),
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset(
            {
                TurnOutcomeKind.DIRECT_RESPONSE,
                TurnOutcomeKind.TASK_DISPATCH,
                TurnOutcomeKind.PLANNER_HANDOFF,
            }
        ),
        may_call_llm=False,
        description="Route deterministic query and transfer fast paths.",
    ),
    GateHandlerSpec(
        id="semantic_router",
        layer=GateLayer.SEMANTIC_ROUTING,
        priority=10,
        handler=_stage_semantic_router,
        owner="semantic_router",
        allowed_owners=frozenset({"guardrail", "query_session", "semantic_router"}),
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        allowed_outcomes=frozenset(
            {
                TurnOutcomeKind.DIRECT_RESPONSE,
                TurnOutcomeKind.TASK_DISPATCH,
                TurnOutcomeKind.PLANNER_HANDOFF,
                TurnOutcomeKind.POLICY_BLOCK,
            }
        ),
        may_call_llm=True,
        description="Run top-level semantic routing before planner fallback.",
        eligibility=all_of(no_quote, task_planner_available),
    ),
)


__all__ = ["GATE_STAGE_SPECS"]
