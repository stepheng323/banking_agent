from collections.abc import Awaitable, Callable
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.contracts import (
    GateEligibilityResult,
    GateHandlerSpec,
    GateLayer,
    GateOutcomeKind,
)
from apps.chat.src.agent.orchestrator.workflows.gate.engine import ordered_gate_handlers, run_gate_engine
from apps.chat.src.agent.orchestrator.workflows.gate.family import build_gate_family_handler
from apps.chat.src.agent.orchestrator.workflows.gate.outcomes import (
    direct_response,
    hint_only,
    planner_handoff,
    policy_block,
    task_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.gate.registry import (
    GATE_HANDLER_SPECS,
)
from apps.chat.src.agent.orchestrator.workflows.gate.runtime import build_gate_runtime
from apps.chat.src.agent.orchestrator.workflows.gate.stage_specs import GATE_STAGE_SPECS
from apps.chat.src.agent.orchestrator.workflows.gate.trace import summarize_gate_trace

_GATE_STAGE_NAMES = (
    "_stage_language_switch",
    "_stage_cancel",
    "_stage_gibberish_filter",
    "_stage_expired_pin",
    "_stage_mixed_supported_unsupported_capability",
    "_stage_capability_boundary_followup",
    "_stage_semantic_unsupported_capability",
    "_stage_schedule_read_router",
    "_stage_resume_prompt_action",
    "_stage_data_plan_reference_purchase",
    "_stage_data_plan_query",
    "_stage_context_frame_followup",
    "_stage_receipt_thread_followup",
    "_stage_support_context_followup",
    "_stage_receipt_request",
    "_stage_beneficiary_suggestion",
    "_stage_contextual_worker_followup",
    "_stage_banking_ambiguity",
    "_stage_support_issue_request",
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
    "banking_ambiguity",
    "query_and_transfer_domain_guards",
}
_GATE_FAMILY_HANDLER_IDS = (
    "preflight_cleanup_family",
    "hard_guardrails_family",
    "capability_guards_family",
    "session_resume_family",
    "specialized_fastpaths_family",
    "context_followups_family",
    "domain_fastpaths_family",
    "semantic_routing_family",
)


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
    handler: Callable[[GateContext], Awaitable[dict[str, Any] | None]],
    *,
    eligibility: Callable[[GateContext], GateEligibilityResult] | None = None,
) -> GateHandlerSpec:
    return GateHandlerSpec(
        id=handler_id,
        layer=layer,
        priority=priority,
        handler=handler,
        owner="unit",
        outcome_kind=GateOutcomeKind.CONTINUE_ONLY,
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


def test_gate_registry_uses_layer_family_handlers() -> None:
    ordered_specs = ordered_gate_handlers(GATE_HANDLER_SPECS)

    assert tuple(spec.id for spec in ordered_specs) == _GATE_FAMILY_HANDLER_IDS
    assert tuple(spec.outcome_kind for spec in ordered_specs) == (GateOutcomeKind.FAMILY_ROUTER,) * len(
        _GATE_FAMILY_HANDLER_IDS
    )


def test_gate_registry_has_unique_ids_and_stable_order() -> None:
    ordered_specs = ordered_gate_handlers(GATE_HANDLER_SPECS)
    ordered_stage_specs = ordered_gate_handlers(GATE_STAGE_SPECS)
    ids = [spec.id for spec in ordered_specs]
    stage_ids = [spec.id for spec in ordered_stage_specs]
    layer_priority_pairs = [(spec.layer, spec.priority) for spec in ordered_specs]
    stage_layer_priority_pairs = [(spec.layer, spec.priority) for spec in ordered_stage_specs]

    assert len(ids) == len(set(ids))
    assert len(stage_ids) == len(set(stage_ids))
    assert len(layer_priority_pairs) == len(set(layer_priority_pairs))
    assert len(stage_layer_priority_pairs) == len(set(stage_layer_priority_pairs))
    assert tuple(ordered_specs) == GATE_HANDLER_SPECS
    assert tuple(ordered_stage_specs) == GATE_STAGE_SPECS
    assert all(spec.description.strip() for spec in ordered_specs)
    assert all(spec.description.strip() for spec in ordered_stage_specs)


def test_gate_registry_metadata_is_reviewable_and_deliberate() -> None:
    specs = (*ordered_gate_handlers(GATE_HANDLER_SPECS), *ordered_gate_handlers(GATE_STAGE_SPECS))
    for spec in specs:
        assert spec.id.strip()
        assert spec.owner.strip()
        assert spec.description.strip()
        assert isinstance(spec.layer, GateLayer)
        assert isinstance(spec.outcome_kind, GateOutcomeKind)
        assert isinstance(spec.priority, int)
        assert spec.priority > 0
        assert callable(spec.handler)
        assert isinstance(spec.may_call_llm, bool)
        assert spec.eligibility is not None or spec.id in _GATE_SPECS_WITH_INTERNAL_ELIGIBILITY


def test_gate_registry_eligibility_metadata_is_non_mutating() -> None:
    specs = (*ordered_gate_handlers(GATE_HANDLER_SPECS), *ordered_gate_handlers(GATE_STAGE_SPECS))
    for spec in specs:
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


async def test_gate_engine_preserves_continue_only_updates_and_falls_back_to_planner() -> None:
    async def cleanup(ctx: GateContext) -> None:
        ctx.gate_updates["pending_interrupt"] = None
        return None

    result = await run_gate_engine(_context(), (_spec("cleanup", GateLayer.PREFLIGHT_CLEANUP, 10, cleanup),))

    assert result.matched_handler_id == "planner_fallback"
    assert result.matched_layer == GateLayer.PLANNER_FALLBACK
    assert result.updates["pending_interrupt"] is None
    assert result.updates["routing_owner"] == "planner"
    assert result.updates["routing_decision"] == "planner_handoff"
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

    async def second(_: GateContext) -> dict[str, Any]:
        calls.append("second")
        return {
            "direct_path_triggered": True,
            "routing_owner": "unit",
            "routing_decision": "matched",
        }

    async def third(_: GateContext) -> dict[str, Any]:
        calls.append("third")
        return {"routing_owner": "unit", "routing_decision": "unexpected"}

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
    assert result.updates["routing_decision"] == "matched"
    assert [entry.handler_id for entry in result.trace] == ["first", "second"]
    assert result.trace[-1].matched is True
    assert result.trace[-1].executed is True
    assert result.trace[-1].routing_owner == "unit"
    assert result.trace[-1].routing_decision == "matched"


async def test_gate_family_router_records_nested_subhandler_trace() -> None:
    calls: list[str] = []

    async def first(_: GateContext) -> None:
        calls.append("first")
        return None

    async def second(_: GateContext) -> dict[str, Any]:
        calls.append("second")
        return {
            "direct_path_triggered": True,
            "routing_owner": "unit",
            "routing_decision": "family_matched",
        }

    family_handler = build_gate_family_handler(
        "unit_family",
        (
            _spec("first", GateLayer.DOMAIN_FASTPATHS, 10, first),
            _spec("second", GateLayer.DOMAIN_FASTPATHS, 20, second),
        ),
    )
    family_spec = GateHandlerSpec(
        id="unit_family",
        layer=GateLayer.DOMAIN_FASTPATHS,
        priority=10,
        handler=family_handler,
        owner="family_router",
        outcome_kind=GateOutcomeKind.FAMILY_ROUTER,
        may_call_llm=False,
        description="Unit family router.",
    )

    result = await run_gate_engine(_context(), (family_spec,))
    summary = summarize_gate_trace(result)

    assert calls == ["first", "second"]
    assert result.matched_handler_id == "unit_family"
    assert result.trace[0].nested_trace[0].handler_id == "first"
    assert result.trace[0].nested_trace[0].matched is False
    assert result.trace[0].nested_trace[1].handler_id == "second"
    assert result.trace[0].nested_trace[1].matched is True
    assert summary["family_traces"] == [
        {
            "handler_id": "unit_family",
            "layer": "domain_fastpaths",
            "entries": [
                {
                    "handler_id": "first",
                    "layer": "domain_fastpaths",
                    "matched": False,
                    "executed": True,
                    "skip_reason": None,
                    "routing_owner": None,
                    "routing_decision": None,
                },
                {
                    "handler_id": "second",
                    "layer": "domain_fastpaths",
                    "matched": True,
                    "executed": True,
                    "skip_reason": None,
                    "routing_owner": "unit",
                    "routing_decision": "family_matched",
                },
            ],
        }
    ]


async def test_gate_engine_skips_ineligible_handler_without_calling_it() -> None:
    calls: list[str] = []

    def ineligible(_: GateContext) -> GateEligibilityResult:
        return GateEligibilityResult(
            eligible=False,
            reason="blocked_for_unit_test",
            details={"why": "not_now"},
        )

    async def skipped(_: GateContext) -> dict[str, Any]:
        calls.append("skipped")
        return {"routing_owner": "unit", "routing_decision": "unexpected"}

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

    async def skipped(_: GateContext) -> dict[str, Any]:
        return {"routing_owner": "unit", "routing_decision": "unexpected"}

    async def matched(_: GateContext) -> dict[str, Any]:
        return {"routing_owner": "unit", "routing_decision": "matched"}

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
    assert summary["routing_owner"] == "unit"
    assert summary["routing_decision"] == "matched"
    assert summary["executed_handler_ids"] == ["matched"]
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
    assert updates["routing_owner"] == "planner"
    assert updates["routing_decision"] == "planner_handoff"
    assert updates["route_source"] == "planner"


def test_direct_response_outcome_sets_standard_routing_fields() -> None:
    updates = direct_response(
        _context(),
        response="Done.",
        owner="guardrail",
        decision="unit_direct",
        semantic_path_shape="unit_direct_shape",
        target_domain="account",
        mode="new",
        route_source="unit_guard",
    )

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "Done."
    assert updates["semantic_path_shape"] == "unit_direct_shape"
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "unit_direct"
    assert updates["routing_target_domain"] == "account"
    assert updates["routing_mode"] == "new"
    assert updates["route_source"] == "unit_guard"


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
        semantic_path_shape="unit_task_shape",
        target_domain="account",
        mode="new",
    )

    assert updates["tasks"] == {"unit_task": spec}
    assert updates["waves"] == [["unit_task"]]
    assert updates["current_wave_index"] == 0
    assert updates["planner_output"] is None
    assert updates["direct_path_triggered"] is True
    assert updates["routing_decision"] == "unit_task_dispatch"


def test_task_dispatch_outcome_can_preserve_absent_semantic_path_shape() -> None:
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

    assert updates["direct_path_triggered"] is True
    assert "semantic_path_shape" not in updates


def test_policy_block_outcome_sets_response_and_guardrail_routing() -> None:
    updates = policy_block(
        _context(),
        response="Not available.",
        decision="capability_blocked",
        semantic_path_shape="unit_policy_block",
        target_domain="data",
    )

    assert updates["direct_path_triggered"] is True
    assert updates["final_response"] == "Not available."
    assert updates["semantic_path_shape"] == "unit_policy_block"
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "capability_blocked"
    assert updates["routing_target_domain"] == "data"


def test_hint_only_outcome_does_not_force_direct_path() -> None:
    updates = hint_only(
        _context(),
        owner="guardrail",
        decision="unit_hint",
        target_domain="support",
        route_source="unit_hint_source",
    )

    assert "direct_path_triggered" not in updates
    assert updates["routing_owner"] == "guardrail"
    assert updates["routing_decision"] == "unit_hint"
    assert updates["routing_target_domain"] == "support"
    assert updates["route_source"] == "unit_hint_source"
