from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    RoutingContractError,
    TurnNextStep,
    TurnOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.contracts import (
    GateEligibilityResult,
    GateHandlerSpec,
    GateLayer,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.engine import ordered_gate_handlers, run_gate_engine
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import (
    direct_response,
    planner_handoff,
    policy_block,
    task_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.runtime import build_gate_runtime
from apps.chat.src.agent.orchestrator.workflows.gate.core.trace import summarize_gate_trace
from apps.chat.src.agent.orchestrator.workflows.gate.stage_specs import GATE_STAGE_SPECS

_GATE_STAGE_NAMES = (
    "_stage_language_switch",
    "_stage_cancel",
    "_stage_gibberish_filter",
    "_stage_expired_pin",
    "_stage_mixed_supported_unsupported_capability",
    "_stage_capability_boundary_followup",
    "_stage_deterministic_unsupported_capability",
    "_stage_pending_interrupt",
    "_stage_schedule_read_router",
    "_stage_resume_prompt_action",
    "_stage_data_plan_reference_purchase",
    "_stage_data_plan_query",
    "_stage_stale_context_arbitration",
    "_stage_recent_transaction_support_request",
    "_stage_support_issue_request",
    "_stage_context_frame_followup",
    "_stage_receipt_thread_followup",
    "_stage_support_context_followup",
    "_stage_receipt_request",
    "_stage_beneficiary_suggestion",
    "_stage_contextual_worker_followup",
    "_stage_banking_ambiguity",
    "_stage_balance_direct",
    "_stage_account_domain",
    "_stage_beneficiary_domain",
    "_stage_airtime_domain",
    "_stage_data_domain",
    "_stage_deterministic_meta",
    "_stage_query_and_transfer_domain_guards",
    "_stage_semantic_router",
)
_GATE_SPECS_WITH_INTERNAL_ELIGIBILITY = {
    "stale_interrupt_cleanup",
    "language_switch",
    "cancel",
    "gibberish_filter",
    "expired_pin",
    "pending_interrupt",
    "banking_ambiguity",
    "query_and_transfer_domain_guards",
}


def _context() -> GateContext:
    state = OrchestratorState(
        user_id="u_gate_engine",
        phone_number="2348011113300",
        channel="whatsapp",
        last_message_text="hello",
        loaded_context={"language": "en"},
    )
    return build_gate_runtime(state, {"configurable": {}, "recursion_limit": 50}).build_context()


def _spec(
    handler_id: str,
    layer: GateLayer,
    priority: int,
    handler: Callable[[GateContext], Awaitable[RouteResolution | None]],
    *,
    eligibility: Callable[[GateContext], GateEligibilityResult] | None = None,
) -> GateHandlerSpec:
    return GateHandlerSpec(
        id=handler_id,
        layer=layer,
        priority=priority,
        handler=handler,
        owner="guardrail",
        outcome_kind=TurnOutcomeKind.PLANNER_HANDOFF,
        may_call_llm=False,
        description=f"Unit test handler {handler_id}",
        eligibility=eligibility,
    )


def test_gate_registry_preserves_stage_order() -> None:
    ordered_specs = ordered_gate_handlers(GATE_STAGE_SPECS)

    assert ordered_specs[0].id == "stale_interrupt_cleanup"
    assert [spec.handler.__name__ for spec in ordered_specs if spec.layer != GateLayer.PREFLIGHT_CLEANUP] == list(
        _GATE_STAGE_NAMES
    )


def test_gate_registry_has_unique_ids_and_stable_order() -> None:
    ordered_stage_specs = ordered_gate_handlers(GATE_STAGE_SPECS)
    stage_ids = [spec.id for spec in ordered_stage_specs]
    stage_layer_priority_pairs = [(spec.layer, spec.priority) for spec in ordered_stage_specs]

    assert len(stage_ids) == len(set(stage_ids))
    assert len(stage_layer_priority_pairs) == len(set(stage_layer_priority_pairs))
    assert tuple(ordered_stage_specs) == GATE_STAGE_SPECS
    assert all(spec.description.strip() for spec in ordered_stage_specs)


def test_gate_registry_metadata_is_reviewable_and_deliberate() -> None:
    for spec in ordered_gate_handlers(GATE_STAGE_SPECS):
        assert spec.id.strip()
        assert spec.owner.strip()
        assert spec.description.strip()
        assert isinstance(spec.layer, GateLayer)
        assert spec.outcome_kind is None or isinstance(spec.outcome_kind, TurnOutcomeKind)
        assert isinstance(spec.priority, int)
        assert spec.priority > 0
        assert callable(spec.handler)
        assert isinstance(spec.may_call_llm, bool)
        assert spec.eligibility is not None or spec.id in _GATE_SPECS_WITH_INTERNAL_ELIGIBILITY


def test_gate_registry_eligibility_metadata_is_non_mutating() -> None:
    for spec in ordered_gate_handlers(GATE_STAGE_SPECS):
        if spec.eligibility is None:
            continue
        ctx = _context()
        before_gate_updates = dict(ctx.gate_updates)

        result = spec.eligibility(ctx)

        assert isinstance(result, GateEligibilityResult)
        assert result.reason.strip()
        assert isinstance(result.details, dict)
        assert ctx.gate_updates == before_gate_updates


def test_ordered_gate_handlers_sorts_by_layer_then_priority() -> None:
    async def noop(_: GateContext) -> None:
        return None

    specs = (
        _spec("semantic", GateLayer.SEMANTIC_ROUTING, 10, noop),
        _spec("hard-late", GateLayer.HARD_GUARDRAILS, 20, noop),
        _spec("hard-early", GateLayer.HARD_GUARDRAILS, 10, noop),
    )

    assert [spec.id for spec in ordered_gate_handlers(specs)] == ["hard-early", "hard-late", "semantic"]


async def test_gate_engine_preserves_cleanup_updates_and_falls_back_to_planner() -> None:
    async def cleanup(ctx: GateContext) -> None:
        ctx.gate_updates["pending_interrupt"] = None
        return None

    result = await run_gate_engine(_context(), (_spec("cleanup", GateLayer.PREFLIGHT_CLEANUP, 10, cleanup),))

    assert result.matched_handler_id == "planner_fallback"
    assert result.matched_layer == GateLayer.PLANNER_FALLBACK
    assert result.updates["pending_interrupt"] is None
    assert result.updates["turn_directive"].owner == "guardrail"
    assert result.updates["turn_directive"].decision == "planner_handoff"
    assert result.updates["turn_directive"].next_step == TurnNextStep.PLAN
    assert result.trace[0].handler_id == "cleanup"
    assert result.trace[0].executed is True
    assert result.trace[0].gate_updates_changed is True
    assert result.trace[-1].handler_id == "planner_fallback"
    assert result.trace[-1].matched is True
    assert result.trace[-1].executed is True


async def test_gate_engine_short_circuits_on_first_match() -> None:
    calls: list[str] = []

    async def first(_: GateContext) -> None:
        calls.append("first")
        return None

    async def second(ctx: GateContext) -> RouteResolution:
        calls.append("second")
        return planner_handoff(ctx, decision="matched", path_shape="unit_matched")

    async def third(ctx: GateContext) -> RouteResolution:
        calls.append("third")
        return planner_handoff(ctx, decision="unexpected", path_shape="unit_unexpected")

    result = await run_gate_engine(
        _context(),
        (
            _spec("first", GateLayer.HARD_GUARDRAILS, 10, first),
            _spec("second", GateLayer.HARD_GUARDRAILS, 20, second),
            _spec("third", GateLayer.HARD_GUARDRAILS, 30, third),
        ),
    )

    assert calls == ["first", "second"]
    assert result.matched_handler_id == "second"
    assert result.matched_layer == GateLayer.HARD_GUARDRAILS
    assert result.updates["turn_directive"].decision == "matched"
    assert [entry.handler_id for entry in result.trace] == ["first", "second"]
    assert result.trace[-1].matched is True
    assert result.trace[-1].executed is True
    assert result.trace[-1].turn_directive is not None
    assert result.trace[-1].turn_directive.owner == "guardrail"
    assert result.trace[-1].turn_directive.decision == "matched"


async def test_gate_engine_skips_ineligible_handler_without_calling_it() -> None:
    calls: list[str] = []

    def ineligible(_: GateContext) -> GateEligibilityResult:
        return GateEligibilityResult(
            eligible=False,
            reason="blocked_for_unit_test",
            details={"why": "not_now"},
        )

    async def skipped(ctx: GateContext) -> RouteResolution:
        calls.append("skipped")
        return planner_handoff(ctx, decision="unexpected", path_shape="unit_unexpected")

    result = await run_gate_engine(
        _context(),
        (_spec("skipped", GateLayer.HARD_GUARDRAILS, 10, skipped, eligibility=ineligible),),
    )

    assert calls == []
    assert result.matched_handler_id == "planner_fallback"
    assert result.trace[0].handler_id == "skipped"
    assert result.trace[0].matched is False
    assert result.trace[0].executed is False
    assert result.trace[0].skip_reason == "blocked_for_unit_test"
    assert result.trace[0].skip_details == {"why": "not_now"}
    assert result.trace[-1].handler_id == "planner_fallback"


async def test_gate_trace_summary_captures_match_execution_and_skips() -> None:
    def ineligible(_: GateContext) -> GateEligibilityResult:
        return GateEligibilityResult(
            eligible=False,
            reason="blocked_for_unit_test",
            details={"why": "not_now"},
        )

    async def skipped(ctx: GateContext) -> RouteResolution:
        return planner_handoff(ctx, decision="unexpected", path_shape="unit_unexpected")

    async def matched(ctx: GateContext) -> RouteResolution:
        return planner_handoff(ctx, decision="matched", path_shape="unit_matched")

    result = await run_gate_engine(
        _context(),
        (
            _spec("skipped", GateLayer.HARD_GUARDRAILS, 10, skipped, eligibility=ineligible),
            _spec("matched", GateLayer.HARD_GUARDRAILS, 20, matched),
        ),
    )

    summary = summarize_gate_trace(result)

    assert summary["matched_handler_id"] == "matched"
    assert summary["matched_layer"] == "hard_guardrails"
    assert summary["routing_owner"] == "guardrail"
    assert summary["routing_decision"] == "matched"
    assert summary["executed_handler_ids"] == ["matched"]
    assert summary["skipped_handler_count"] == 1
    assert summary["skipped_handlers"] == [
        {
            "handler_id": "skipped",
            "layer": "hard_guardrails",
            "reason": "blocked_for_unit_test",
            "details": {"why": "not_now"},
        }
    ]


async def test_gate_engine_default_eligibility_preserves_handler_execution() -> None:
    calls: list[str] = []

    async def handler(_: GateContext) -> None:
        calls.append("handler")
        return None

    result = await run_gate_engine(
        _context(),
        (_spec("handler", GateLayer.HARD_GUARDRAILS, 10, handler),),
    )

    assert calls == ["handler"]
    assert result.trace[0].executed is True
    assert result.trace[0].skip_reason is None
    assert result.matched_handler_id == "planner_fallback"


def test_planner_handoff_outcome_matches_fallback_shape() -> None:
    ctx = _context()
    ctx.gate_updates["pending_interrupt"] = None
    ctx.summary_updates = {"turn_context_summary": {"focus": "account"}}

    updates = planner_handoff(ctx)

    assert updates["pending_interrupt"] is None
    assert updates["turn_context_summary"] == {"focus": "account"}
    assert updates["turn_directive"].owner == "guardrail"
    assert updates["turn_directive"].decision == "planner_handoff"
    assert updates["turn_directive"].source == "planner_fallback"
    assert updates["turn_directive"].next_step == TurnNextStep.PLAN


def test_direct_response_outcome_sets_standard_routing_fields() -> None:
    updates = direct_response(
        _context(),
        response="Done.",
        owner="guardrail",
        decision="unit_direct",
        path_shape="unit_direct_shape",
        target_domain="account",
        mode="new",
        source="unit_guard",
    )

    assert updates["final_response"] == "Done."
    assert updates["turn_directive"].owner == "guardrail"
    assert updates["turn_directive"].decision == "unit_direct"
    assert updates["turn_directive"].target_domain == "account"
    assert updates["turn_directive"].mode == "new"
    assert updates["turn_directive"].source == "unit_guard"
    assert updates["turn_directive"].path_shape == "unit_direct_shape"
    assert updates["turn_directive"].next_step == TurnNextStep.FINALIZE


def test_task_dispatch_outcome_sets_task_wave_and_routing_fields() -> None:
    spec = TaskSpec(
        id="unit_task",
        type="account",
        stage=TaskStage.DRAFT,
        payload={"action": "check_balance"},
    )

    updates = task_dispatch(
        _context(),
        tasks={"unit_task": spec},
        waves=[["unit_task"]],
        owner="guardrail",
        decision="unit_task_dispatch",
        path_shape="unit_task_shape",
        target_domain="account",
        mode="new",
    )

    assert updates["tasks"] == {"unit_task": spec}
    assert updates["waves"] == [["unit_task"]]
    assert updates["current_wave_index"] == 0
    assert updates["planner_output"] is None
    assert updates["turn_directive"].decision == "unit_task_dispatch"
    assert updates["turn_directive"].path_shape == "unit_task_shape"
    assert updates["turn_directive"].next_step == TurnNextStep.ADVANCE


def test_task_dispatch_outcome_can_preserve_absent_path_shape() -> None:
    spec = TaskSpec(
        id="unit_task",
        type="beneficiary",
        stage=TaskStage.DRAFT,
        payload={"action": "save_beneficiary"},
    )

    updates = task_dispatch(
        _context(),
        tasks={"unit_task": spec},
        waves=[["unit_task"]],
        owner="guardrail",
        decision="unit_task_dispatch",
    )

    assert updates["turn_directive"].path_shape == "unit_task_dispatch"


def test_policy_block_outcome_sets_response_and_guardrail_routing() -> None:
    updates = policy_block(
        _context(),
        response="Not available.",
        decision="capability_blocked",
        path_shape="unit_policy_block",
        target_domain="data",
    )

    assert updates["final_response"] == "Not available."
    assert updates["turn_directive"].owner == "guardrail"
    assert updates["turn_directive"].decision == "capability_blocked"
    assert updates["turn_directive"].target_domain == "data"
    assert updates["turn_directive"].path_shape == "unit_policy_block"
    assert updates["turn_directive"].next_step == TurnNextStep.FINALIZE


def test_hint_replacement_is_an_explicit_planner_handoff() -> None:
    updates = planner_handoff(
        _context(),
        owner="guardrail",
        decision="unit_hint",
        target_domain="support",
        source="unit_hint_source",
        path_shape="unit_hint_handoff",
    )

    assert updates["turn_directive"].owner == "guardrail"
    assert updates["turn_directive"].decision == "unit_hint"
    assert updates["turn_directive"].target_domain == "support"
    assert updates["turn_directive"].source == "unit_hint_source"
    assert updates["turn_directive"].path_shape == "unit_hint_handoff"
    assert updates["turn_directive"].outcome_kind == TurnOutcomeKind.PLANNER_HANDOFF
    assert updates["turn_directive"].next_step == TurnNextStep.PLAN


async def test_gate_engine_rejects_untyped_matched_stage_result() -> None:
    async def invalid(_: GateContext) -> dict[str, Any]:
        return {"final_response": "bypassed contract"}

    spec = _spec(
        "invalid",
        GateLayer.HARD_GUARDRAILS,
        10,
        invalid,  # type: ignore[arg-type]
    )

    with pytest.raises(RoutingContractError, match="expected RouteResolution"):
        await run_gate_engine(_context(), (spec,))


async def test_gate_engine_rejects_owner_not_declared_by_stage() -> None:
    async def invalid(ctx: GateContext) -> RouteResolution:
        return planner_handoff(ctx, owner="semantic_router")

    with pytest.raises(RoutingContractError, match="allowed owners"):
        await run_gate_engine(
            _context(),
            (_spec("invalid_owner", GateLayer.HARD_GUARDRAILS, 10, invalid),),
        )


async def test_gate_engine_rejects_outcome_not_declared_by_stage() -> None:
    async def invalid(ctx: GateContext) -> RouteResolution:
        return direct_response(
            ctx,
            response="No.",
            owner="guardrail",
            decision="invalid_outcome",
            path_shape="unit",
        )

    with pytest.raises(RoutingContractError, match="allowed outcomes"):
        await run_gate_engine(
            _context(),
            (_spec("invalid_outcome", GateLayer.HARD_GUARDRAILS, 10, invalid),),
        )


async def test_gate_engine_rejects_next_step_not_declared_by_stage() -> None:
    async def invalid(ctx: GateContext) -> RouteResolution:
        return planner_handoff(ctx)

    spec = _spec("invalid_step", GateLayer.HARD_GUARDRAILS, 10, invalid)
    spec = replace(
        spec,
        allowed_next_steps=frozenset({TurnNextStep.FINALIZE}),
    )

    with pytest.raises(RoutingContractError, match="allowed next steps"):
        await run_gate_engine(_context(), (spec,))
