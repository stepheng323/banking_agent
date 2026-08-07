import json
from datetime import date

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.contracts import (
    SelectionPayload,
    SurfaceItemView,
    SurfaceSection,
    SurfaceView,
    SurfaceViewMode,
    VarianceDriversEvidenceSelection,
)
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryFrame,
    QueryRequest,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    PendingClarificationState,
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryParseResult,
    QueryRequestShape,
    QueryTimeRange,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.query.services.reasoning.models import (
    FocusedItemDecision,
    QuerySemanticDecision,
    RepairDecision,
    SemanticReasonerContext,
    TransactionListDecision,
)
from banking.transactions.query.services.reasoning.reasoner import QuerySemanticReasoner
from shared.types.query_preferences import QueryPreferenceUpdate
from tests.query.factories import make_query_request


def _query_ir(**kwargs: object) -> QueryRequest:
    fallback_day = date(2026, 3, 20)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


class _FailingStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise AssertionError("LLM should not be called")


class _FailingLLM:
    def with_structured_output(self, schema: object) -> _FailingStructured:
        del schema
        return _FailingStructured()


class _TrackingStructured:
    def __init__(self, decision: QuerySemanticDecision) -> None:
        self.decision = decision
        self.calls = 0
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> QuerySemanticDecision:
        self.prompts.append(prompt)
        self.calls += 1
        return self.decision


class _TrackingLLM:
    def __init__(self, decision: QuerySemanticDecision) -> None:
        self.structured = _TrackingStructured(decision)

    def with_structured_output(self, schema: object) -> _TrackingStructured:
        del schema
        return self.structured


def _direct_answer_surface_view(**context: object) -> SurfaceView:
    return SurfaceView(mode=SurfaceViewMode.DIRECT_ANSWER, context=context)


def _transaction_list_surface_view(**context: object) -> SurfaceView:
    return SurfaceView(mode=SurfaceViewMode.TRANSACTION_LIST, context=context)


def test_narrow_reasoner_adapter_ignores_unrecognized_provider_fields() -> None:
    """Schema drift must not turn a visible-item follow-up into a new query."""
    decision = TransactionListDecision.model_validate(
        {
            "decision": "continuation",
            "continuation_type": "drill_down",
            "target_amount": 25000,
            # A legacy parser field occasionally echoed by the provider. It
            # is not part of the narrow continuation contract.
            "request_shape": "detail",
        }
    )

    public = decision.to_public_decision()

    assert public.decision == "continuation"
    assert public.continuation_type == "drill_down"
    assert public.target_amount == 25000


def test_focused_reasoner_adapter_preserves_recipient_followup() -> None:
    """Recipient refinements must survive the focused-item LLM schema."""
    decision = FocusedItemDecision.model_validate(
        {
            "decision": "continuation",
            "continuation_type": "recipient_drill_down",
            "followup_intent": "none",
            "recipient_name": "mum",
        }
    )

    public = decision.to_public_decision()

    assert public.continuation_type == "recipient_drill_down"
    assert public.recipient_name == "mum"


def test_focused_reasoner_adapter_preserves_complete_replacement_extraction() -> None:
    decision = FocusedItemDecision.model_validate(
        {
            "decision": "new_query",
            "confidence": 0.94,
            "extraction": {
                "intent": "transaction_search",
                "filters": {"recipient": "Uber", "transaction_type": "debit"},
                "time_range": {"reference_type": "explicit", "period": "last_month"},
                "request_shape": "existence",
            },
        }
    )

    public = decision.to_public_decision()

    assert public.decision == "new_query"
    assert public.extraction is not None
    assert public.extraction.filters.recipient == "Uber"
    assert public.extraction.filters.transaction_type == "debit"
    assert public.extraction.time_range.period == "last_month"
    assert public.extraction.request_shape == QueryRequestShape.EXISTENCE


def test_narrow_reasoner_adapter_preserves_explicit_preference_update() -> None:
    decision = TransactionListDecision.model_validate(
        {
            "decision": "continuation",
            "continuation_type": "update_preferences",
            "preferences_update": {"presentation_detail": "detailed"},
        }
    )

    public = decision.to_public_decision()

    assert public.preferences_update == QueryPreferenceUpdate(presentation_detail="detailed")


def test_repair_reasoner_adapter_preserves_canonical_scope_delta() -> None:
    decision = RepairDecision.model_validate(
        {
            "decision": "continuation",
            "confidence": 0.91,
            "continuation_type": "repair",
            "followup_intent": "refine_existing",
            "repair_delta": {
                "direction_mutation": "replace",
                "direction": "credit",
                "dimension": "account",
            },
        }
    )

    public = decision.to_public_decision()

    assert public.repair_delta is not None
    assert public.repair_delta.direction == "credit"
    assert public.repair_delta.dimension == "account"


def _transaction_surface_item(index: int = 1) -> SurfaceItemView:
    return SurfaceItemView(
        id=f"txn-{index}",
        label=f"Payment {index}",
        amount=1000.0 * index,
        payload=SelectionPayload(
            selection_kind="transaction", entity_type="transaction", entity_id=f"txn-{index}", label=f"Payment {index}"
        ),
        metadata={"bank_name": "Access Bank", "status": "failed"},
    )


def _grouped_summary_surface_view(**context: object) -> SurfaceView:
    return SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context=context)


@pytest.mark.asyncio
async def test_explicit_correction_selects_compact_repair_prompt_profile() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            continuation_type="repair",
        )
    )

    await QuerySemanticReasoner(llm).reason(
        SemanticReasonerContext(
            message="No, use income instead",
            today=date(2026, 3, 13),
            language="en",
            query_request=_query_ir(),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    rendered_prompt = str(llm.structured.prompts[-1])
    assert "Repair rules:" in rendered_prompt
    assert "Grouped-summary rules:" not in rendered_prompt


def _composite_surface_view() -> SurfaceView:
    item = _transaction_surface_item(1).model_copy(update={"metadata": {"step_id": "evidence"}})
    return SurfaceView(
        mode=SurfaceViewMode.COMPOSITE,
        items=[item],
        sections=[
            SurfaceSection(
                step_id="evidence",
                role="evidence",
                mode=SurfaceViewMode.TRANSACTION_LIST,
                items=[item],
            )
        ],
    )


@pytest.mark.asyncio
async def test_composite_ordinal_selection_is_deterministic_and_retains_step() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show the first one",
            today=date(2026, 3, 13),
            language="en",
            query_request=_query_ir(),
            surface_view=_composite_surface_view(),
        )
    )

    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_index == 0
    assert decision.target_step_id == "evidence"
    assert decision.semantic_llm_used is False


@pytest.mark.asyncio
async def test_composite_semantic_followup_uses_targeted_section_contract() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            continuation_type="repair",
            target_step_id="evidence",
        )
    )

    decision = await QuerySemanticReasoner(llm).reason(
        SemanticReasonerContext(
            message="Use last month for the evidence section",
            today=date(2026, 3, 13),
            language="en",
            query_request=_query_ir(),
            surface_view=_composite_surface_view(),
        )
    )

    assert decision.target_step_id == "evidence"
    assert llm.structured.calls == 1


def _contract(
    query: QueryRequest,
) -> QueryRequest:
    assert query.time_range is not None
    return query.model_copy(deep=True)


def test_reasoner_serializes_focused_surface_contract() -> None:
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="bene_1",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id="bene_1",
                    label="Acme Corp",
                    filters_patch={"counterparty": ["Acme Corp"]},
                    fact_capabilities=["date", "reference", "bank"],
                ),
            )
        ],
        context={
            "mode": "direct_answer",
            "focus_type": "beneficiary",
            "selected_item_id": "bene_1",
        },
    )

    payload = json.loads(QuerySemanticReasoner._serialize_surface_snapshot(surface_view=surface_view))

    assert payload["focus_type"] == "beneficiary"
    assert payload["selected_item_id"] == "bene_1"
    assert payload["focused_item"]["selection_kind"] == "beneficiary"
    assert payload["focused_item"]["fact_capabilities"] == ["date", "reference", "bank"]
    assert payload["focused_item"]["has_filters_patch"] is True


def test_reasoner_surface_snapshot_includes_direct_answer_focus_context() -> None:
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        lead_text="That was with Acme Corp.",
        items=[
            SurfaceItemView(
                id="txn-acme",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="txn-acme",
                    label="Acme Corp",
                    fact_capabilities=["date", "amount", "bank", "reference"],
                ),
                metadata={
                    "date": "2026-06-28",
                    "bank_name": "Zenith Bank",
                    "counterparty": "Acme Corp",
                    "type": "credit",
                },
            )
        ],
        context={"type": "single_transaction", "focus_type": "transaction", "selected_item_id": "txn-acme"},
    )

    snapshot = QuerySemanticReasoner._serialize_surface_snapshot(surface_view=surface_view)

    assert "That was with Acme Corp." in snapshot
    assert "2026-06-28" in snapshot
    assert "date" in snapshot
    assert "Acme Corp" in snapshot


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_receipt_action_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "get_receipt"


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_first_item_detail_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = SurfaceView(
        mode=SurfaceViewMode.TRANSACTION_LIST,
        items=[_transaction_surface_item(1), _transaction_surface_item(2)],
        context={"type": "transaction_list"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Show the first one",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "view_details"
    assert decision.drill_down_index == 0
    assert decision.semantic_llm_used is False


@pytest.mark.asyncio
async def test_reasoner_resolves_a_variance_driver_to_typed_evidence_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        items=[
            SurfaceItemView(
                id="spending-category-food",
                label="Food",
                payload=SelectionPayload(
                    selection_kind="group_bucket",
                    entity_type="variance_driver",
                    entity_id="spending:category:food",
                    label="Food",
                    insight_evidence=VarianceDriversEvidenceSelection(
                        basis="ledger_transactions",
                        measure="spending",
                        dimension="category",
                        bucket_key="food",
                        metric="spending",
                        current_start="2023-11-01",
                        current_end="2023-11-30",
                        baseline_start="2023-10-01",
                        baseline_end="2023-10-31",
                    ),
                ),
            )
        ],
        context={"view": "insight"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Show the food transactions behind that change.",
            today=date(2026, 7, 26),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.INSIGHT,
                    time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 26)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.continuation_type == "show_evidence"
    assert decision.followup_intent == "refine_existing"
    assert decision.target_text == "Food"


@pytest.mark.asyncio
async def test_reasoner_uses_semantic_contract_for_visible_bank_fact() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="semantic_visible_bank_fact",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            drill_down_index=0,
            fact_field="bank",
            requested_field="bank",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[_transaction_surface_item(1)],
        context={"type": "single_transaction"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What bank was that?",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "answer_fact"
    assert decision.fact_field == "bank"
    assert decision.requested_field == "bank"
    assert decision.drill_down_index == 0
    assert decision.semantic_llm_used is True
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_does_not_shortcut_fresh_credit_total_as_focused_amount_fact() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.96,
            reason="semantic_credit_total_query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                raw_query="How much came in this month",
            ),
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="bene_1",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id="bene_1",
                    label="Acme Corp",
                    filters_patch={"counterparty": ["Acme Corp"]},
                    fact_capabilities=["date", "amount", "bank", "reference"],
                ),
            )
        ],
        context={"focus_type": "beneficiary", "selected_item_id": "bene_1"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="How much came in this month",
            today=date(2026, 6, 27),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.BENEFICIARY_SUMMARY,
                    time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 27)),
                    filters=Filters(transaction_type="credit"),
                    aggregation=Aggregation(type="sum", sort_by="amount"),
                    result_limit=1,
                )
            ),
            surface_view=surface_view,
        )
    )

    assert llm.structured.calls == 1
    assert decision.decision == "new_query"
    assert decision.continuation_type is None
    assert decision.drill_down_action is None
    assert decision.fact_field is None


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_show_more_details() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_view_details",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="view_details",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show more details",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "view_details"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_new_query_for_recent_transaction_reset() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.94,
            reason="llm_fresh_list_reset",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show my recent transactions",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "new_query"
    assert decision.reason == "llm_fresh_list_reset"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_new_query_for_recent_transaction_reset_with_explicit_period() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.94,
            reason="llm_fresh_list_reset",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show my recent transactions in the last 2 weeks",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "new_query"
    assert decision.reason == "llm_fresh_list_reset"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_new_query_for_day_scoped_singular_list_reset() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.94,
            reason="llm_fresh_list_reset",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show today's transaction",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "new_query"
    assert decision.reason == "llm_fresh_list_reset"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_scoped_recipient_delta() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_scoped_recipient_delta",
            continuation_type="recipient_drill_down",
            followup_intent="none",
            recipient_name="tolu",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What about tolu?",
            today=date(2026, 4, 6),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 4, 1), end=date(2026, 4, 6)),
                    filters=Filters(counterparty=["Mum"], transaction_type="debit"),
                    result_limit=1,
                    result_reference="latest",
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "recipient_drill_down"
    assert decision.reason == "llm_scoped_recipient_delta"
    assert decision.recipient_name == "tolu"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_surface_view_for_deterministic_receipt_action_without_surface_fallbacks() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        context={"type": "single_transaction"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "get_receipt"


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_active_result_fact_answer() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_fact_status",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="answer_fact",
            fact_field="status",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")
    item = QueryResultItem(
        id="txn-1",
        description="Payment to Mum",
        amount=10000.0,
        date=date(2026, 3, 13),
        metadata={"status": "processing", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="was it successful?",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
            items=[item],
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "answer_fact"
    assert decision.fact_field == "status"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_active_result_conversational_reaction() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            reason="llm_conversational_reaction",
            continuation_type="conversational",
            response_text="It is on the high side.",
            contextual_hint="You can ask what made it up.",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _grouped_summary_surface_view(type="spending_total")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="That's a lot",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "conversational"
    assert decision.response_text == "It is on the high side."
    assert decision.contextual_hint == "You can ask what made it up."
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_active_result_appreciation_reaction() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            reason="llm_appreciation_reaction",
            continuation_type="conversational",
            response_text="Anytime. What should we handle next?",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _transaction_list_surface_view(type="transaction_list")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Nice",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "conversational"
    assert decision.response_text == "Anytime. What should we handle next?"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_ends_active_result_session_for_thank_you_with_emoji_without_llm() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            reason="should_not_be_used",
            continuation_type="conversational",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _grouped_summary_surface_view(type="spending_total")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Thank you 😊",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "end_session"
    assert decision.reason == "deterministic_end_session"
    assert llm.structured.calls == 0


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_beneficiary_summary_followup() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_beneficiary_fact_followup",
            continuation_type="recipient_drill_down",
            recipient_name="Adesanya Kunle",
            fact_field="date",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    item = QueryResultItem(
        id="bene_1",
        description="Adesanya Kunle",
        amount=50000.0,
        date=date(2026, 3, 27),
        metadata={"recipient_name": "Adesanya Kunle"},
    )
    surface_view = SurfaceView(
        mode=SurfaceViewMode.GROUPED_SUMMARY,
        items=[
            SurfaceItemView(
                id="bene_1",
                label="Adesanya Kunle",
                amount=50000.0,
                count=1,
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id="bene_1",
                    label="Adesanya Kunle",
                ),
            )
        ],
        context={"view": "beneficiary_summary"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="When was Kunle's transaction?",
            today=date(2026, 3, 28),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.BENEFICIARY_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 14), end=date(2026, 3, 28)),
                )
            ),
            items=[item],
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "recipient_drill_down"
    assert decision.recipient_name == "Adesanya Kunle"
    assert decision.fact_field == "date"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_logs_deterministic_surface_action_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.drill_down_action == "get_receipt"
    assert (
        "query_surface_action_deterministic",
        {"reasoner_context_mode": "active_result", "action": "get_receipt", "reason": "deterministic_receipt"},
    ) in events
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "continuation",
            "reasoner_context_mode": "active_result",
            "reasoner_llm_used": False,
            "continuation_type": "drill_down",
            "confidence": 1.0,
            "reason": "deterministic_receipt",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_logs_llm_fact_answer_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.91,
            reason="llm_fact_recipient",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="answer_fact",
            fact_field="recipient",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")
    item = QueryResultItem(
        id="txn-1",
        description="Payment to Mum",
        amount=10000.0,
        date=date(2026, 3, 13),
        metadata={"status": "processing", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="who was it to?",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
            items=[item],
        )
    )

    assert decision.fact_field == "recipient"
    assert llm.structured.calls == 1
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "continuation",
            "reasoner_context_mode": "active_result",
            "reasoner_llm_used": True,
            "continuation_type": "drill_down",
            "confidence": 0.91,
            "reason": "llm_fact_recipient",
        },
    ) in events
    query_trace_events = [payload for event, payload in events if event == "query_trace"]
    assert query_trace_events
    assert query_trace_events[0]["query_phase"] == "semantic_reasoner"
    assert query_trace_events[0]["llm_used"] is True
    assert query_trace_events[0]["prompt_item_count"] == 1


@pytest.mark.asyncio
async def test_reasoner_logs_llm_backed_fresh_query_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.93,
            reason="llm_fresh_query",
            extraction=QueryExtractionResult(raw_query="can you show my last transaction", result_limit=1),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="can you show my last transaction",
            today=date(2026, 3, 13),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
        )
    )

    assert llm.structured.calls == 1
    assert decision.decision == "fresh_query"
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "fresh_query",
            "reasoner_context_mode": "active_result",
            "reasoner_llm_used": True,
            "continuation_type": None,
            "confidence": 0.93,
            "reason": "llm_fresh_query",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_passes_through_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.92,
            reason="llm_replace_scope",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="for last week only",
            today=date(2026, 3, 14),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_possessive_week_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_replace_scope_this_week_possessive",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Only this week's",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 2, 17), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 16)
    assert decision.time_range.end == date(2026, 3, 19)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_contrastive_last_week_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_replace_scope_last_week_contrastive",
            continuation_type="time_delta",
            followup_intent="replace_scope",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What about the week prior",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit", merchant=["mum"]),
                )
            ),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is None
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_today_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_replace_scope_today",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 19), end=date(2026, 3, 19), granularity="day"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="fetch today only",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 19)
    assert decision.time_range.end == date(2026, 3, 19)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_contrastive_yesterday_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_replace_scope_yesterday_contrastive",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 18), end=date(2026, 3, 18), granularity="day"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="what about the day before",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 18)
    assert decision.time_range.end == date(2026, 3, 18)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_single_item_contrastive_yesterday_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.92,
            reason="llm_single_item_replace_scope_yesterday",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="no transaction the day prior?",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 17), end=date(2026, 3, 19)),
                    result_limit=1,
                    result_reference="latest",
                )
            ),
            surface_view=_direct_answer_surface_view(type="single_transaction"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.extraction is not None
    assert decision.extraction.time_range is not None
    assert decision.extraction.time_range.period == "yesterday"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_contrastive_last_week_replace_scope_with_extraction() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_replace_scope_last_week_contrastive_extraction",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What about the week before",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit", merchant=["mum"]),
                )
            ),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.extraction is not None
    assert decision.extraction.time_range is not None
    assert decision.extraction.time_range.period == "last_week"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_continue_pagination_without_llm() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="llm_continue_pagination",
            continuation_type="show_more",
            followup_intent="continue_pagination",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="next page",
            today=date(2026, 3, 14),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "show_more"
    assert decision.followup_intent == "continue_pagination"
    assert llm.structured.calls == 0


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_previous_pagination_without_llm() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="llm_previous_pagination",
            continuation_type="show_more",
            followup_intent="previous_pagination",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="previous page",
            today=date(2026, 3, 14),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "show_more"
    assert decision.followup_intent == "previous_pagination"
    assert llm.structured.calls == 0


@pytest.mark.asyncio
async def test_reasoner_passes_through_show_evidence_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_show_aggregate_evidence",
            continuation_type="show_evidence",
            followup_intent="refine_existing",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Show me",
            today=date(2026, 3, 14),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                    filters=Filters(transaction_type="debit"),
                    aggregation=Aggregation(type="sum"),
                )
            ),
            surface_view=_grouped_summary_surface_view(view="summary"),
        )
    )

    assert decision.continuation_type == "show_evidence"
    assert decision.followup_intent == "refine_existing"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_grouped_total_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.91,
            reason="llm_grouped_total_followup",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="so what the total?",
            today=date(2026, 3, 30),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.BENEFICIARY_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 30)),
                    filters=Filters(transaction_type="debit"),
                    aggregation=Aggregation(type="sum", sort_by="count"),
                )
            ),
            surface_view=_grouped_summary_surface_view(view="summary"),
        )
    )

    assert decision.continuation_type == "grouped_total_followup"
    assert decision.followup_intent == "refine_existing"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_unclear_followup_contract() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.31,
            reason="ambiguous_followup",
            continuation_type="unclear",
            followup_intent="none",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="for last week only",
            today=date(2026, 3, 14),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    assert decision.continuation_type == "unclear"
    assert decision.followup_intent == "none"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_last_month_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="llm_replace_scope_last_month",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 28), granularity="month"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="for last month only",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 1, 1), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 2, 1)
    assert decision.time_range.end == date(2026, 2, 28)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_logs_single_llm_trace_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.91,
            reason="llm_replace_scope",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_period="last week",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    await reasoner.reason(
        SemanticReasonerContext(
            message="can we compare that against last week",
            today=date(2026, 3, 14),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    query_trace_events = [payload for event, payload in events if event == "query_trace"]
    assert query_trace_events
    assert query_trace_events[0]["reasoner_schema"] == "active_continuation"
    assert query_trace_events[0]["llm_calls_used"] == 1
    assert query_trace_events[0]["single_llm_invariant"] is True
    assert isinstance(query_trace_events[0]["context_bytes"], int)
    assert query_trace_events[0]["context_bytes"] > 0
    llm_call_events = [payload for event, payload in events if event == "query_reasoner_llm_call"]
    assert llm_call_events
    assert llm_call_events[0]["reasoner_schema"] == "active_continuation"
    assert llm_call_events[0]["prompt_surface_type"] == "list"
    assert isinstance(llm_call_events[0]["context_bytes"], int)
    assert llm_call_events[0]["context_bytes"] > 0


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_pending_clarification_time_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="clarification_answer",
            confidence=0.9,
            reason="llm_pending_time_reply",
            time_period="last 3 days",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="last 3 days",
            today=date(2026, 3, 13),
            language="en",
            pending_clarification=PendingClarificationState(
                original_query="How much did I spend last",
                current_intent=QueryIntent.ANALYTICS_SUMMARY,
                original_extraction=QueryExtractionResult(raw_query="How much did I spend last"),
                resolver_message="What time period did you mean by 'last'?",
                language="en",
            ),
        )
    )

    assert decision.decision == "clarification_answer"
    assert llm.structured.calls == 1
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "clarification_answer",
            "reasoner_context_mode": "pending_clarification",
            "reasoner_llm_used": True,
            "continuation_type": None,
            "confidence": 0.9,
            "reason": "llm_pending_time_reply",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_bounds_surface_items_and_omits_unneeded_frames() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_bounded_prompt",
            continuation_type="time_delta",
            followup_intent="replace_scope",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    items = [
        QueryResultItem(
            id=f"txn-{index}",
            description=f"Payment {index}",
            amount=1000.0 + index,
            date=date(2026, 3, 13),
            metadata={"status": "success", "bank_name": "Zenith"},
        )
        for index in range(6)
    ]
    query_frames = [
        QueryFrame(
            frame_id=f"qf_{index}",
            turn_index=index + 1,
            summary_text=f"summary {index}",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
        )
        for index in range(5)
    ]

    await reasoner.reason(
        SemanticReasonerContext(
            message="what about the week before",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                )
            ),
            items=items,
            surface_view=_transaction_list_surface_view(type="transaction_list", count=5),
            query_frames=query_frames,
        )
    )

    assert llm.structured.calls == 1
    assert llm.structured.prompts
    prompt_str = str(llm.structured.prompts[0])
    assert "Payment 0" in prompt_str
    assert "Payment 4" in prompt_str
    assert "Payment 5" not in prompt_str
    assert "summary 4" not in prompt_str
    assert "summary 2" not in prompt_str
    assert "summary 1" not in prompt_str


@pytest.mark.asyncio
async def test_reasoner_bounds_frames_for_historical_profile() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_historical_prompt",
            continuation_type="aggregate",
            followup_intent="refine_existing",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    query_frames = [
        QueryFrame(
            frame_id=f"qf_{index}",
            turn_index=index + 1,
            summary_text=f"summary {index}",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
        )
        for index in range(5)
    ]

    await reasoner.reason(
        SemanticReasonerContext(
            message="compare that with the earlier result",
            today=date(2026, 3, 19),
            language="en",
            query_request=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                )
            ),
            query_frames=query_frames,
        )
    )

    prompt_str = str(llm.structured.prompts[0])
    assert "summary 4" in prompt_str
    assert "summary 2" in prompt_str
    assert "summary 1" not in prompt_str


@pytest.mark.asyncio
async def test_extraction_step_preserves_session_for_conversational_reaction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 14)),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="conversational",
            response_text="It is on the high side.",
            contextual_hint="You can ask what made it up.",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "That's a lot",
            "language": "en",
            "today": date(2026, 3, 14),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "You spent money in that period.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(type="spending_total").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["response"] == "It is on the high side.\n\nYou can ask what made it up."
    assert result.patch["session_active"] is False
    assert result.patch["flow_state"] == "complete"
    assert result.patch["_query_session_transition"] == "exit_query_session_conversational"


@pytest.mark.asyncio
async def test_active_query_explicit_preference_becomes_typed_operation_handoff() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 14)),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="update_preferences",
            preferences_update=QueryPreferenceUpdate(presentation_detail="detailed"),
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    result = await step.run(
        {
            "message": "Always give me detailed transaction answers",
            "language": "en",
            "today": date(2026, 3, 14),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "You spent money in that period.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(type="spending_total").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "complete"
    assert result.patch["session_active"] is True
    assert result.patch["query_preferences_handoff"] == {"presentation_detail": "detailed"}


@pytest.mark.asyncio
async def test_extraction_step_fresh_query_uses_deterministic_parser_without_parser_parse() -> None:
    step = ExtractionStep(_FailingLLM())
    parsed_result = QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=QueryExtractionResult(
            raw_query="show my last transaction",
            result_limit=1,
            result_reference="latest",
        ),
        query_request=_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 2, 11), end=date(2026, 3, 13)),
                result_limit=1,
                result_reference="latest",
            )
        ).model_dump(mode="json"),
    )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("fresh query should not call parser.parse")

    step.parser.parse_deterministic = lambda *args, **kwargs: parsed_result  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "show my last transaction",
            "language": "en",
            "today": date(2026, 3, 13),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_fresh_query_falls_back_to_parser_parse_when_not_deterministic() -> None:
    step = ExtractionStep(_FailingLLM())
    parsed_extraction = QueryExtractionResult(
        raw_query="show my last transaction",
        result_limit=1,
        result_reference="latest",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 2, 11), end=date(2026, 3, 13)),
        result_limit=1,
        result_reference="latest",
    )

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "show my last transaction"
        return QueryParseResult(
            outcome=ResolverOutcome.OK,
            extraction=parsed_extraction,
            query_request=_contract(parsed_query).model_dump(mode="json"),
        )

    step.parser.parse_deterministic = lambda *args, **kwargs: None  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "show my last transaction",
            "language": "en",
            "today": date(2026, 3, 13),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_does_not_reparse_support_problem_as_query_continuation() -> None:
    step = ExtractionStep(_FailingLLM())
    today = date(2026, 3, 13)
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=today),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.95,
            reason="misread support issue as query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                raw_query="I was debited but they didn't receive it",
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today"),
            ),
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {
            "message": "I was debited but they didn't receive it",
            "language": "en",
            "today": today,
        },
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == "I'm not sure what you're referring to. Could you rephrase?"
    assert "query_request" not in updates


@pytest.mark.asyncio
async def test_extraction_step_active_result_aggregate_can_reuse_reasoner_extraction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
            filters=Filters(transaction_type="debit"),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            extraction=QueryExtractionResult(
                raw_query="how much did i spend",
                result_limit=None,
                filters=QueryFilters(transaction_type="debit"),
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("aggregate continuation should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "how much did i spend",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "Transactions",
                    "items": [QueryResultItem(description="Txn", amount=1000, date=date(2026, 3, 13)).model_dump()],
                    "surface_view": _transaction_list_surface_view().model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_active_result_new_query_compiles_without_parser_parse() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
                raw_query="How much did I spend this week",
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("active-session new_query should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "How much did I spend this week",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "You spent money in that period.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(type="spending_total").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_active_result_incomplete_new_query_does_not_reparse() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.8,
            reason="incomplete replacement",
            semantic_llm_used=True,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "Which account did I spend from most this month?",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "Transactions",
                    "items": [],
                    "surface_view": _transaction_list_surface_view().model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.patch["_query_reasoner_to_parser_suppressed"] is True


@pytest.mark.asyncio
async def test_extraction_step_grouped_total_followup_compiles_spend_vs_earn_to_cash_flow() -> None:
    """In-vs-out compare on an analytics grouped surface compiles to cash flow."""
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(),
            aggregation=Aggregation(type="breakdown", group_by="transaction_type"),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "Did I spend more than I earned this month?",
            "language": "en",
            "today": date(2026, 7, 24),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "Breakdown by transaction type.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(group_by="transaction_type").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
    assert result.patch["query_request"].intent == QueryIntent.CASH_FLOW_SUMMARY
    assert result.patch["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_active_summary_spend_vs_earn_preempts_single_item_reasoning() -> None:
    """A direct total remains a summary scope for a later cash-flow question."""
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(transaction_type="credit"),
            aggregation=Aggregation(type="sum"),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="amount",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Did I spend more than I earned this month?", "today": date(2026, 7, 24), "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "Income this month.", "items": []},
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.CASH_FLOW_SUMMARY
    assert query_request.time_start == date(2026, 7, 1)
    assert query_request.time_end == date(2026, 7, 24)


@pytest.mark.asyncio
async def test_grouped_account_rank_preserves_grounded_summary_scope() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="breakdown", group_by="account", limit=5),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            rank="largest",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Which account did I spend from most?", "today": date(2026, 7, 24), "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "Spending by account.", "items": []},
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.aggregation is not None
    assert query_request.aggregation.group_by == "account"
    assert query_request.aggregation.limit == 1


@pytest.mark.asyncio
async def test_grouped_account_followup_does_not_let_advisory_clarification_override_scope() -> None:
    """A typed aggregate continuation owns the grouped surface even if rank is omitted."""
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="breakdown", group_by="account", limit=5),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="ask_clarify",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Which account did I spend from most?", "today": date(2026, 7, 24), "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "Spending by account.", "items": []},
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert query_request.aggregation is not None
    assert query_request.aggregation.group_by == "account"
    assert query_request.aggregation.limit == 5


@pytest.mark.asyncio
async def test_grouped_followup_without_typed_target_replays_safe_grouped_scope() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="breakdown", group_by="account", limit=5),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    updates = await step._handle_continuation(
        {"message": "Which account was highest?", "today": date(2026, 7, 24), "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "Spending by account.", "items": []},
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.group_by == "account"
    assert query_request.aggregation.limit == 5


@pytest.mark.asyncio
async def test_grouped_total_followup_compiles_typed_regroup_extraction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(transaction_type="credit"),
            aggregation=Aggregation(type="breakdown", group_by="category", limit=5),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            confidence=0.98,
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=QueryFilters(transaction_type="credit"),
                aggregation=QueryAggregation(type="breakdown", group_by="account"),
                request_shape=QueryRequestShape.ANALYTICS,
            ),
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    result = await step.run(
        {
            "message": "Break that down by account and include the overall total",
            "language": "en",
            "today": date(2026, 7, 24),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "Income by category.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(group_by="category").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
    request = result.patch["query_request"]
    assert request.aggregation is not None
    assert request.aggregation.group_by == "account"
    assert request.filters is not None
    assert request.filters.transaction_type == "credit"


@pytest.mark.asyncio
async def test_active_query_affordability_uses_deterministic_read_contract() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 7, 20), end=date(2026, 7, 24)),
        )
    )

    result = await step.run(
        {
            "message": "Can I send 35k?",
            "language": "en",
            "today": date(2026, 7, 24),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {"summary_text": "Transactions", "items": []},
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["query_request"].intent == QueryIntent.AFFORDABILITY
    assert result.patch["_query_llm_calls_used"] == 0


@pytest.mark.asyncio
async def test_extraction_step_grouped_total_followup_beneficiary_summary_still_totals() -> None:
    """Recipient-grouped summaries keep the original total-over-scope behavior."""
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 30)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum", sort_by="count"),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "so what the total?",
            "language": "en",
            "today": date(2026, 3, 30),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "Top recipients.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(view="summary").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
    assert result.patch["query_request"].intent == QueryIntent.ANALYTICS_SUMMARY
    assert result.patch["query_request"].aggregation.type == "sum"


@pytest.mark.asyncio
async def test_extraction_step_grouped_total_followup_non_compare_clarifies_with_response() -> None:
    """Unsupported grouped-total follow-ups clarify with a non-empty response."""
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 24)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="breakdown", group_by="category"),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "what about the one before?",
            "language": "en",
            "today": date(2026, 7, 24),
            "query_session": {
                "session_active": True,
                "query_request": session_contract,
                "query_result": {
                    "summary_text": "Spending by category.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(group_by="category").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.response
