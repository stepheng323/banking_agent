"""Compile bounded LLM plan drafts into canonical read contracts."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from banking.transactions.query.models.conversation import (
    QueryPlanBinding,
    QueryPlanStep,
    QueryTurnPlan,
)
from banking.transactions.query.models.extraction import (
    ParserQueryExtraction,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryParseResult,
    QueryPlanBindingDraft,
    QueryPlanDraft,
    QueryPlanStepDraft,
    QueryRequestShape,
    QueryStepExtraction,
    ResolverOutcome,
)
from banking.transactions.query.models.operations import QueryRequest


class QueryPlanCompileError(ValueError):
    """The proposed plan cannot be executed safely."""


def compile_query_plan_draft(
    parser: Any,
    draft: QueryPlanDraft,
    *,
    today: date,
    language: str,
    raw_query: str,
) -> QueryTurnPlan:
    steps: list[QueryPlanStep] = []
    for draft_step in draft.steps:
        extraction = QueryExtractionResult(
            **draft_step.extraction.model_dump(mode="python"),
            raw_query=raw_query,
        )
        parsed = parser._finalize_extraction(extraction, today=today, language=language)
        if parsed.outcome not in {ResolverOutcome.OK, ResolverOutcome.NEGOTIATED} or not parsed.query_request:
            raise QueryPlanCompileError("a query plan step needs clarification")
        try:
            request = QueryRequest.model_validate(parsed.query_request)
        except Exception as exc:
            raise QueryPlanCompileError("a query plan step is invalid") from exc
        steps.append(
            QueryPlanStep(
                step_id=draft_step.step_id,
                request=request,
                role=draft_step.role,
                depends_on=draft_step.depends_on,
                required=draft_step.required,
                bindings=[QueryPlanBinding.model_validate(binding.model_dump()) for binding in draft_step.bindings],
            )
        )
    if len({step.request.model_dump_json() for step in steps}) < 2:
        raise QueryPlanCompileError("the request should use one ordinary query")
    try:
        return QueryTurnPlan(steps=steps)
    except Exception as exc:
        raise QueryPlanCompileError("the query plan shape is invalid") from exc


def compile_query_plan_result(
    parser: Any,
    draft: QueryPlanDraft,
    *,
    today: date,
    language: str,
    raw_query: str,
) -> QueryParseResult:
    """Compile a plan and expose its primary request through the existing parser contract."""
    plan = compile_query_plan_draft(
        parser,
        draft,
        today=today,
        language=language,
        raw_query=raw_query,
    )
    primary = next(step for step in plan.steps if step.role == "primary")
    primary_draft = next(step for step in draft.steps if step.step_id == primary.step_id)
    return QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=parser._inflate_parser_extraction(
            ParserQueryExtraction(
                **primary_draft.extraction.model_dump(mode="python"),
                evidence_mode="none",
            ),
            question=raw_query,
            language=language,
        ),
        query_request=primary.request.model_dump(mode="json"),
        execution_contract=plan.model_dump(mode="json"),
    )


def compile_primary_with_evidence_result(
    parser: Any,
    extraction: ParserQueryExtraction,
    *,
    today: date,
    language: str,
    raw_query: str,
) -> QueryParseResult:
    """Compile a primary analytical answer plus its requested source rows.

    The LLM identifies that evidence is part of the same user turn through a
    flat boolean.  The runtime derives the evidence request from the primary
    typed extraction, so the model does not have to repeat and potentially
    drift the period, filters, or direction in a deeply nested plan.
    """

    primary_payload = extraction.model_dump(
        mode="python",
        exclude={"plan", "evidence_mode"},
    )
    primary_step = QueryPlanStepDraft(
        step_id="primary",
        role="primary",
        extraction=QueryStepExtraction.model_validate(primary_payload),
    )

    evidence_filters = extraction.filters.model_copy(deep=True)
    binding: QueryPlanBindingDraft | None = None
    if evidence_filters.category is None and evidence_filters.recipient is None and evidence_filters.bank is None:
        binding_target: Literal["category", "counterparty", "account"] | None = None
        group_by = extraction.aggregation.group_by if extraction.aggregation is not None else None
        if group_by in {"category"}:
            binding_target = "category"
        elif group_by in {"recipient", "counterparty"}:
            binding_target = "counterparty"
        elif group_by in {"bank", "account"}:
            binding_target = "account"
        elif extraction.insight is not None:
            dimensions = list(extraction.insight.dimensions or [])
            for dimension in dimensions:
                if dimension == "category":
                    binding_target = "category"
                    break
                if dimension == "counterparty":
                    binding_target = "counterparty"
                    break
                if dimension == "account":
                    binding_target = "account"
                    break
        if binding_target is not None:
            binding = QueryPlanBindingDraft(
                source_step_id="primary",
                source="top_group",
                target=binding_target,
            )

    evidence_step = QueryPlanStepDraft(
        step_id="evidence",
        role="evidence",
        extraction=QueryStepExtraction(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=QueryFilters.model_validate(evidence_filters.model_dump(mode="python")),
            time_range=extraction.time_range.model_copy(deep=True),
            request_shape=QueryRequestShape.LIST,
            result_limit=5,
        ),
        depends_on=["primary"] if binding is not None else [],
        required=False,
        bindings=[binding] if binding is not None else [],
    )
    return compile_query_plan_result(
        parser,
        QueryPlanDraft(steps=[primary_step, evidence_step]),
        today=today,
        language=language,
        raw_query=raw_query,
    )


__all__ = [
    "QueryPlanCompileError",
    "compile_primary_with_evidence_result",
    "compile_query_plan_draft",
    "compile_query_plan_result",
]
