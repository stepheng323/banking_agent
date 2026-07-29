from datetime import date

import pytest
from pydantic import ValidationError

from banking.transactions.query.continuations.repair import QueryRepairError, apply_query_scope_delta
from banking.transactions.query.continuations.repair_resolution import resolve_pending_proposal, resolve_repair
from banking.transactions.query.conversation_focus import advance_focus, focus_for_request, resolve_focus
from banking.transactions.query.grounding.frames import build_query_frame
from banking.transactions.query.models.conversation import (
    PendingInterpretationProposal,
    QueryInterpretationProposal,
    QueryPlanBinding,
    QueryPlanStep,
    QueryScopeDelta,
    QueryTurnPlan,
    SingleQueryExecution,
)
from banking.transactions.query.models.domain import QueryIntent, QueryResult
from banking.transactions.query.models.extraction import (
    ParserQueryExtraction,
    PendingClarificationState,
    QueryAggregation,
    QueryExtractionResult,
    QueryPlanDraft,
    QueryPlanStepDraft,
    QueryRequestShape,
    QueryStepExtraction,
    QueryTimeRange,
    TimeReference,
)
from banking.transactions.query.models.operations import GroupedSummarySpec, TransactionPredicate
from banking.transactions.query.services.parsing.parser import QueryParser
from banking.transactions.query.session_state import (
    build_query_session_v3,
    pending_input_from_legacy,
    project_query_session_v3,
)
from banking.transactions.query.turn_plan import execute_query_turn_plan
from tests.query.factories import query_scope, retrieve_request, summarize_request


def _request():
    return retrieve_request(
        query_scope(
            date(2026, 7, 1),
            date(2026, 7, 29),
            predicate=TransactionPredicate(categories=["food"], direction="debit"),
        )
    )


def test_scope_delta_preserves_unmentioned_scope_and_replaces_direction() -> None:
    request = _request()

    updated = apply_query_scope_delta(
        request,
        QueryScopeDelta(direction_mutation="replace", direction="credit"),
    )

    assert updated.scope is not None
    assert updated.scope.predicate.direction == "credit"
    assert updated.scope.predicate.categories == ["food"]
    assert updated.period == request.period


def test_scope_delta_can_clear_a_category_scope() -> None:
    updated = apply_query_scope_delta(_request(), QueryScopeDelta(category_mutation="clear"))

    assert updated.scope is not None
    assert updated.scope.predicate.categories == []
    assert updated.scope.predicate.direction == "debit"


def test_scope_delta_rejects_unsupported_summary_repair() -> None:
    with pytest.raises(QueryRepairError):
        apply_query_scope_delta(_request(), QueryScopeDelta(dimension="account"))


def test_query_plan_requires_backward_dependency_and_one_primary_step() -> None:
    request = _request()
    with pytest.raises(ValidationError):
        QueryTurnPlan(
            steps=[
                QueryPlanStep(step_id="one", request=request, role="primary", depends_on=["two"]),
                QueryPlanStep(step_id="two", request=request, role="evidence"),
            ]
        )

    plan = QueryTurnPlan(
        steps=[
            QueryPlanStep(step_id="one", request=request, role="primary"),
            QueryPlanStep(
                step_id="two",
                request=request,
                role="evidence",
                depends_on=["one"],
                bindings=[QueryPlanBinding(source_step_id="one", source="top_group", target="category")],
            ),
        ]
    )
    assert plan.steps[1].bindings[0].source_step_id == "one"


def test_focus_stays_with_last_user_refinement_when_evidence_is_displayed() -> None:
    summary_request = summarize_request(
        query_scope(date(2026, 7, 1), date(2026, 7, 29)),
        GroupedSummarySpec(measure="spending", statistic="sum", dimension="account"),
    )
    frame = build_query_frame(
        query_request=summary_request,
        result=QueryResult(summary_text="By account"),
        turn_index=1,
    )
    active = focus_for_request(summary_request, frame_id=frame.frame_id, source="user_refinement")

    resolved = resolve_focus(frames=[frame], active_focus=active)

    assert resolved is not None
    assert resolved.dimension == "account"
    assert resolved.source == "user_refinement"


def test_display_only_continuation_preserves_semantic_focus() -> None:
    request = _request()
    previous = focus_for_request(request, frame_id="qf_1", source="user_refinement", turn_id="turn_1")

    advanced = advance_focus(
        request=request,
        previous=previous,
        continuation_type="show_evidence",
        turn_id="turn_2",
    )

    assert advanced.frame_id == "qf_1"
    assert advanced.source == "user_refinement"
    assert advanced.latest_user_turn_id == "turn_2"


def test_explicit_selection_becomes_semantic_focus() -> None:
    from banking.transactions.query.contracts import SelectionPayload

    request = _request()
    selected = SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id="tx_2",
        label="Second transaction",
    )

    advanced = advance_focus(
        request=request,
        previous=focus_for_request(request, frame_id="qf_1"),
        continuation_type="drill_down",
        selected_payload=selected,
        source_frame_id="qf_1",
        turn_id="turn_2",
    )

    assert advanced.source == "user_selection"
    assert advanced.selected_payload == selected


def test_new_refinement_frame_takes_focus_and_retains_lineage() -> None:
    request = _request()
    focus = advance_focus(
        request=request,
        previous=focus_for_request(request, frame_id="qf_1"),
        continuation_type="filter_delta",
        turn_id="turn_2",
    )
    result = QueryResult(summary_text="Refined transactions", query_request=request, conversation_focus=focus)

    frame = build_query_frame(query_request=request, result=result, turn_index=2)

    assert frame.focus is not None
    assert frame.focus.frame_id == "qf_2"
    assert frame.focus.source == "user_refinement"
    assert frame.source_frame_id == "qf_1"


def test_proposal_is_bounded_to_two_grounded_contracts() -> None:
    request = _request()
    proposal = QueryInterpretationProposal(
        proposal_id="whole",
        contract=SingleQueryExecution(request=request),
        confidence=0.6,
    )
    pending = PendingInterpretationProposal(proposals=[proposal, proposal.model_copy(update={"proposal_id": "food"})])

    assert len(pending.proposals) == 2


def test_high_confidence_repair_executes_without_a_second_parser() -> None:
    updates = resolve_repair(
        request=_request(),
        primary=QueryScopeDelta(direction_mutation="replace", direction="credit"),
        alternate=None,
        confidence=0.92,
        locale="en",
        session={},
        source_frame_id="qf_1",
        turn_id="turn_2",
    )

    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "repair"
    assert updates["query_request"].scope.predicate.direction == "credit"


def test_materially_different_repair_creates_two_grounded_proposals() -> None:
    updates = resolve_repair(
        request=_request(),
        primary=QueryScopeDelta(category_mutation="clear"),
        alternate=QueryScopeDelta(direction_mutation="replace", direction="credit"),
        confidence=0.61,
        locale="en",
        session={},
        source_frame_id="qf_1",
        turn_id="turn_2",
    )

    assert updates["flow_state"] == "parsing"
    assert updates["pending_query_input"]["kind"] == "interpretation_proposal"
    assert len(updates["pending_query_input"]["proposals"]) == 2


def test_proposal_selection_executes_only_the_chosen_contract() -> None:
    request = _request()
    first = QueryInterpretationProposal(
        proposal_id="one",
        contract=SingleQueryExecution(request=request),
        confidence=0.6,
    )
    changed = apply_query_scope_delta(request, QueryScopeDelta(direction_mutation="replace", direction="credit"))
    pending = PendingInterpretationProposal(
        proposals=[first, first.model_copy(update={"proposal_id": "two", "contract": SingleQueryExecution(request=changed)})]
    )

    updates = resolve_pending_proposal(pending, "second", locale="en", session={})

    assert updates is not None
    assert updates["query_request"].scope.predicate.direction == "credit"


def test_v3_checkpoint_persists_field_input_without_flat_clarification_authority() -> None:
    legacy = PendingClarificationState(
        original_query="",
        current_intent=QueryIntent.TRANSACTION_LIST,
        original_extraction=QueryExtractionResult(intent=QueryIntent.TRANSACTION_LIST),
        clarification_type="time",
        target_field="time",
    )
    pending = pending_input_from_legacy(legacy)
    assert pending is not None

    session = build_query_session_v3(
        request=_request(),
        result=None,
        raw_frames=[],
        pending_input=pending,
    )
    persisted = session.model_dump(mode="json")

    assert persisted["schema_version"] == 3
    assert persisted["pending_input"]["kind"] == "field_clarification"
    assert "pending_clarification" not in persisted
    projected = project_query_session_v3(persisted)
    assert projected is not None
    assert projected["pending_clarification"]["kind"] == "pending_clarification"


def test_v3_plan_projection_exposes_primary_request_without_losing_plan() -> None:
    primary = _request()
    supporting = retrieve_request(query_scope(date(2026, 6, 1), date(2026, 6, 29)))
    plan = QueryTurnPlan(
        steps=[
            QueryPlanStep(step_id="current", request=primary, role="primary"),
            QueryPlanStep(step_id="baseline", request=supporting, role="supporting"),
        ]
    )
    persisted = build_query_session_v3(
        request=primary,
        result=None,
        raw_frames=[],
        execution_contract=plan,
    ).model_dump(mode="json")

    projected = project_query_session_v3(persisted)

    assert projected is not None
    assert projected["query_request"] == primary.model_dump(mode="json")
    assert projected["execution_contract"]["kind"] == "plan"


def test_composite_repair_updates_only_the_targeted_plan_step() -> None:
    primary = _request()
    supporting = retrieve_request(
        query_scope(
            date(2026, 7, 1),
            date(2026, 7, 29),
            predicate=TransactionPredicate(direction="debit"),
        )
    )
    plan = QueryTurnPlan(
        steps=[
            QueryPlanStep(step_id="food", request=primary, role="primary"),
            QueryPlanStep(step_id="overall", request=supporting, role="supporting"),
        ]
    )

    updates = resolve_repair(
        request=supporting,
        primary=QueryScopeDelta(direction_mutation="replace", direction="credit"),
        alternate=None,
        confidence=0.9,
        locale="en",
        session={},
        source_frame_id="qf_1",
        turn_id="turn_2",
        execution_contract=plan,
        target_step_id="overall",
    )

    repaired_plan = updates["execution_contract"]
    assert isinstance(repaired_plan, QueryTurnPlan)
    assert repaired_plan.steps[0].request == primary
    assert repaired_plan.steps[1].request.scope.predicate.direction == "credit"


@pytest.mark.asyncio
async def test_turn_plan_executes_in_order_and_binds_top_group() -> None:
    summary = summarize_request(
        query_scope(date(2026, 7, 1), date(2026, 7, 29)),
        GroupedSummarySpec(measure="spending", statistic="sum", dimension="category"),
    )
    evidence = retrieve_request(query_scope(date(2026, 7, 1), date(2026, 7, 29)))
    plan = QueryTurnPlan(
        steps=[
            QueryPlanStep(step_id="summary", request=summary, role="primary"),
            QueryPlanStep(
                step_id="evidence",
                request=evidence,
                role="evidence",
                depends_on=["summary"],
                bindings=[QueryPlanBinding(source_step_id="summary", source="top_group", target="category")],
            ),
        ]
    )
    calls = []

    async def execute(request):
        calls.append(request)
        if len(calls) == 1:
            from banking.transactions.query.contracts import (
                SelectionPayload,
                SurfaceItemView,
                SurfaceView,
                SurfaceViewMode,
            )

            return QueryResult(
                summary_text="Food is highest",
                query_request=request,
                surface_view=SurfaceView(
                    mode=SurfaceViewMode.GROUPED_SUMMARY,
                    items=[
                        SurfaceItemView(
                            id="food",
                            label="Food",
                            amount=1000,
                            payload=SelectionPayload(
                                selection_kind="group_bucket", entity_type="category", entity_id="food", label="Food", group_key="food"
                            ),
                        )
                    ],
                ),
            )
        return QueryResult(summary_text="Food transactions", query_request=request)

    result = await execute_query_turn_plan(plan, execute)

    assert len(calls) == 2
    assert calls[1].scope.predicate.categories == ["food"]
    assert result.surface_view is not None
    assert result.surface_view.mode.value == "composite"
    frame = build_query_frame(
        query_request=summary,
        result=QueryResult(
            summary_text=result.summary_text,
            query_request=summary,
            surface_view=result.surface_view,
            execution_contract=plan,
        ),
        turn_index=1,
    )
    assert frame.visible_items[0]["step_id"] == "summary"
    assert frame.visible_items[0]["section_role"] == "primary"


@pytest.mark.asyncio
async def test_fresh_parser_compiles_bounded_plan_in_its_existing_call() -> None:
    period = QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month")
    draft = ParserQueryExtraction(
        plan=QueryPlanDraft(
            steps=[
                QueryPlanStepDraft(
                    step_id="by_account",
                    role="primary",
                    extraction=QueryStepExtraction(
                        intent=QueryIntent.ANALYTICS_SUMMARY,
                        request_shape=QueryRequestShape.ANALYTICS,
                        time_range=period,
                        aggregation=QueryAggregation(type="breakdown", group_by="bank"),
                    ),
                ),
                QueryPlanStepDraft(
                    step_id="overall",
                    role="supporting",
                    extraction=QueryStepExtraction(
                        intent=QueryIntent.ANALYTICS_SUMMARY,
                        request_shape=QueryRequestShape.ANALYTICS,
                        time_range=period,
                        aggregation=QueryAggregation(type="sum"),
                    ),
                ),
            ]
        )
    )

    class Structured:
        async def ainvoke(self, prompt, config=None):
            del prompt, config
            return draft

    class LLM:
        def with_structured_output(self, schema):
            del schema
            return Structured()

    result = await QueryParser(LLM()).parse(
        "Break down my spending by account and give me the overall total",
        today=date(2026, 7, 29),
        language="en",
    )

    assert result.execution_contract is not None
    assert result.execution_contract["kind"] == "plan"
    assert len(result.execution_contract["steps"]) == 2
