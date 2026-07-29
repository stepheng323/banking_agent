"""Compile bounded LLM plan drafts into canonical read contracts."""

from __future__ import annotations

from datetime import date
from typing import Any

from banking.transactions.query.models.conversation import (
    QueryPlanBinding,
    QueryPlanStep,
    QueryTurnPlan,
)
from banking.transactions.query.models.extraction import (
    ParserQueryExtraction,
    QueryExtractionResult,
    QueryParseResult,
    QueryPlanDraft,
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
            ParserQueryExtraction(**primary_draft.extraction.model_dump(mode="python")),
            question=raw_query,
            language=language,
        ),
        query_request=primary.request.model_dump(mode="json"),
        execution_contract=plan.model_dump(mode="json"),
    )


__all__ = ["QueryPlanCompileError", "compile_query_plan_draft", "compile_query_plan_result"]
