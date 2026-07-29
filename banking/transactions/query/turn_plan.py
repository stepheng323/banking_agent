"""Deterministic executor for bounded multi-step query plans."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from banking.transactions.query.continuations.repair import QueryRepairError, apply_query_scope_delta
from banking.transactions.query.contracts import SurfaceSection, SurfaceView, SurfaceViewMode
from banking.transactions.query.models.conversation import (
    QueryPlanBinding,
    QueryPlanStep,
    QueryScopeDelta,
    QueryTurnPlan,
)
from banking.transactions.query.models.domain import QueryResult, QuerySectionResult, QueryTurnResult
from banking.transactions.query.models.operations import QueryRequest

ExecuteRequest = Callable[[QueryRequest], Awaitable[QueryResult]]


@dataclass(frozen=True, slots=True)
class QueryPlanExecutionError:
    step_id: str
    reason: str


def _binding_value(binding: QueryPlanBinding, result: QueryResult) -> str | float | dict[str, str] | None:
    if binding.source == "period":
        period = result.query_request.period if result.query_request is not None else None
        if period is None:
            return None
        return {"start": period.start.isoformat(), "end": period.end.isoformat(), "granularity": period.granularity}
    if binding.source == "scalar":
        if result.items and len(result.items) == 1:
            return result.items[0].amount
        return None
    surface = result.surface_view
    if surface is None or not surface.items:
        return None
    item = surface.items[0]
    if binding.source == "selected_group":
        # A plan has no interactive selection; a selected-group binding is
        # valid only when a preceding request explicitly produced one item.
        if len(surface.items) != 1:
            return None
    payload = item.payload
    return payload.group_key or payload.entity_id or item.label


def _apply_binding(
    request: QueryRequest,
    binding: QueryPlanBinding,
    value: str | float | dict[str, str],
) -> QueryRequest:
    if binding.target == "category":
        return apply_query_scope_delta(request, QueryScopeDelta(category_mutation="replace", categories=[str(value)]))
    if binding.target == "counterparty":
        return apply_query_scope_delta(
            request,
            QueryScopeDelta(counterparty_mutation="replace", counterparty=str(value)),
        )
    if binding.target == "account":
        return apply_query_scope_delta(request, QueryScopeDelta(account_mutation="replace", account_names=[str(value)]))
    if binding.target == "amount":
        return apply_query_scope_delta(request, QueryScopeDelta(amount_mutation="replace", min_amount=float(value)))
    if binding.target == "period" and isinstance(value, dict):
        return apply_query_scope_delta(request, QueryScopeDelta(period_mutation="replace", period=value))
    raise QueryRepairError("the plan binding cannot be applied")


def _bound_request(step: QueryPlanStep, completed: dict[str, QueryResult]) -> QueryRequest:
    request = step.request
    for binding in step.bindings:
        prior = completed.get(binding.source_step_id)
        if prior is None:
            raise QueryRepairError("a required plan source is unavailable")
        value = _binding_value(binding, prior)
        if value is None:
            raise QueryRepairError("the plan source did not produce a usable value")
        request = _apply_binding(request, binding, value)
    return request


async def execute_query_turn_plan(plan: QueryTurnPlan, execute_request: ExecuteRequest) -> QueryTurnResult:
    """Execute a max-three-step read plan without any generative composition."""
    completed: dict[str, QueryResult] = {}
    sections: list[QuerySectionResult] = []
    surface_sections: list[SurfaceSection] = []
    primary_summary = ""
    for step in plan.steps:
        try:
            request = _bound_request(step, completed)
            result = await execute_request(request)
        except Exception:
            reason = "unavailable"
            sections.append(QuerySectionResult(step_id=step.step_id, role=step.role, unavailable_reason=reason))
            surface_sections.append(
                SurfaceSection(
                    step_id=step.step_id,
                    role=step.role,
                    mode=SurfaceViewMode.DIRECT_ANSWER,
                    unavailable_reason=reason,
                )
            )
            if step.required:
                break
            continue

        completed[step.step_id] = result
        sections.append(QuerySectionResult(step_id=step.step_id, role=step.role, result=result))
        surface = result.surface_view
        if surface is not None:
            surface_sections.append(
                SurfaceSection(
                    step_id=step.step_id,
                    role=step.role,
                    mode=surface.mode,
                    lead_text=surface.lead_text,
                    items=surface.items,
                )
            )
        if step.role == "primary":
            primary_summary = result.summary_text

    return QueryTurnResult(
        execution_contract=plan,
        summary_text=primary_summary,
        sections=sections,
        has_more=any(section.result.has_more for section in sections if section.result is not None),
        surface_view=SurfaceView(mode=SurfaceViewMode.COMPOSITE, lead_text=primary_summary, sections=surface_sections),
    )


__all__ = ["QueryPlanExecutionError", "execute_query_turn_plan"]
