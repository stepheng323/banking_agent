from datetime import date

import pytest
from pydantic import ValidationError

from banking.transactions.query.continuations.repair import QueryRepairError, apply_query_scope_delta
from banking.transactions.query.continuations.repair_resolution import resolve_pending_proposal, resolve_repair
from banking.transactions.query.conversation_focus import focus_for_request, resolve_focus
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
from banking.transactions.query.models.extraction import PendingClarificationState, QueryExtractionResult
from banking.transactions.query.models.operations import GroupedSummarySpec, TransactionPredicate
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
