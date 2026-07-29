"""Deterministic application of unified query repair deltas."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import cast

from banking.transactions.query.models.conversation import QueryScopeDelta
from banking.transactions.query.models.operations import (
    AllAccounts,
    AmountRange,
    AnalyzeOperation,
    CompareOperation,
    CounterpartySelector,
    Money,
    NamedAccount,
    NamedCounterparty,
    QueryOperation,
    QueryRequest,
    ResolvedPeriod,
    RetrieveOperation,
    SummarizeOperation,
)


class QueryRepairError(ValueError):
    """A user-facing repair cannot safely be represented by the source contract."""


def _mutate_values(current: Sequence[str], mutation: str | None, values: Sequence[str], field: str) -> list[str]:
    if mutation is None:
        return list(current)
    if mutation == "clear":
        return []
    if mutation == "replace":
        return list(dict.fromkeys(values))
    if mutation == "add":
        return list(dict.fromkeys([*current, *values]))
    if mutation == "remove":
        remove = {value.casefold() for value in values}
        return [value for value in current if value.casefold() not in remove]
    raise QueryRepairError(f"{field} does not support mutation {mutation}")


def _require_value(mutation: str | None, value: object | None, field: str) -> None:
    if mutation in {"replace", "add", "remove"} and value is None:
        raise QueryRepairError(f"{field} requires a value")


def apply_query_scope_delta(request: QueryRequest, delta: QueryScopeDelta) -> QueryRequest:
    """Return a validated immutable repair of ``request``.

    This function intentionally has no text parsing or account lookup.  The
    reasoner provides sparse typed intent, and the caller resolves any named
    account against the user's linked accounts before executing the result.
    """
    operation = request.operation
    if not isinstance(operation, (RetrieveOperation, SummarizeOperation, CompareOperation, AnalyzeOperation)):
        raise QueryRepairError("the current query does not support scope repair")

    scope = operation.scope
    predicate = scope.predicate

    if delta.period_mutation is not None:
        if delta.period_mutation != "replace" or delta.period is None:
            raise QueryRepairError("period repair must provide a replacement range")
        try:
            period = ResolvedPeriod.model_validate(delta.period)
        except Exception as exc:
            raise QueryRepairError("the requested period is invalid") from exc
        scope = scope.model_copy(update={"period": period})

    if delta.account_mutation is not None:
        if delta.account_mutation == "all" or delta.account_mutation == "clear":
            scope = scope.model_copy(update={"accounts": AllAccounts()})
        elif delta.account_mutation == "replace" and len(delta.account_names) == 1:
            scope = scope.model_copy(update={"accounts": NamedAccount(name=delta.account_names[0])})
        else:
            raise QueryRepairError("account repair requires one resolved account or all accounts")

    if delta.counterparty_mutation is not None:
        _require_value(delta.counterparty_mutation, delta.counterparty, "counterparty")
        if delta.counterparty_mutation == "clear":
            predicate = predicate.model_copy(update={"counterparty": None})
        elif delta.counterparty_mutation == "replace":
            predicate = predicate.model_copy(
                update={
                    "counterparty": CounterpartySelector(
                        role="any", reference=NamedCounterparty(name=str(delta.counterparty))
                    )
                }
            )
        else:
            raise QueryRepairError("counterparty repair only supports replace or clear")

    if delta.direction_mutation is not None:
        _require_value(delta.direction_mutation, delta.direction, "direction")
        if delta.direction_mutation == "clear":
            predicate = predicate.model_copy(update={"direction": None})
        elif delta.direction_mutation == "replace":
            predicate = predicate.model_copy(update={"direction": delta.direction})
        else:
            raise QueryRepairError("direction repair only supports replace or clear")

    categories = _mutate_values(predicate.categories, delta.category_mutation, delta.categories, "category")
    statuses = _mutate_values(predicate.statuses, delta.status_mutation, delta.statuses, "status")
    event_types = _mutate_values(predicate.event_types, delta.event_type_mutation, delta.event_types, "event type")
    exclusions = _mutate_values(predicate.exclusions, delta.exclusion_mutation, delta.exclusions, "exclusion")
    predicate = predicate.model_copy(
        update={
            "categories": categories,
            "statuses": cast(list[object], statuses),
            "event_types": event_types,
            "exclusions": exclusions,
        }
    )

    if delta.amount_mutation is not None:
        if delta.amount_mutation == "clear":
            predicate = predicate.model_copy(update={"amount": None})
        elif delta.amount_mutation == "replace":
            if delta.min_amount is None and delta.max_amount is None:
                raise QueryRepairError("amount repair requires a minimum or maximum")
            predicate = predicate.model_copy(
                update={
                    "amount": AmountRange(
                        minimum=Money(amount=Decimal(str(delta.min_amount))) if delta.min_amount is not None else None,
                        maximum=Money(amount=Decimal(str(delta.max_amount))) if delta.max_amount is not None else None,
                    )
                }
            )
        else:
            raise QueryRepairError("amount repair only supports replace or clear")

    scope = scope.model_copy(update={"predicate": predicate})

    updated_operation: QueryOperation
    if isinstance(operation, RetrieveOperation):
        if any(value is not None for value in (delta.measure, delta.statistic, delta.dimension, delta.rank)):
            raise QueryRepairError("the current transaction list cannot be regrouped by this repair")
        updated_operation = operation.model_copy(update={"scope": scope})
    elif isinstance(operation, SummarizeOperation):
        summary_updates: dict[str, object] = {}
        for field in ("measure", "statistic", "dimension", "rank"):
            value = getattr(delta, field)
            if value is None:
                continue
            target = "rank_by" if field == "rank" else field
            summary_updates[target] = value
        if delta.cardinality is not None:
            summary_updates["answer_cardinality"] = delta.cardinality
        try:
            summary = operation.summary.model_copy(update=summary_updates)
        except Exception as exc:
            raise QueryRepairError("that summary change is not supported") from exc
        updated_operation = operation.model_copy(update={"scope": scope, "summary": summary})
    elif isinstance(operation, CompareOperation):
        if any(value is not None for value in (delta.measure, delta.statistic, delta.dimension, delta.rank)):
            raise QueryRepairError("that comparison change is not supported")
        updated_operation = operation.model_copy(update={"scope": scope})
    else:
        analysis_updates: dict[str, object] = {}
        for field in ("measure", "analysis_basis", "confidence_policy", "completeness_policy"):
            value = getattr(delta, field)
            if value is not None:
                analysis_updates[field] = value
        if delta.dimension is not None:
            analysis_updates["dimensions"] = [delta.dimension]
        try:
            analysis = operation.analysis.model_copy(update=analysis_updates)
        except Exception as exc:
            raise QueryRepairError("that insight change is not supported") from exc
        updated_operation = operation.model_copy(update={"scope": scope, "analysis": analysis})

    try:
        payload = request.model_dump(mode="python")
        payload["operation"] = updated_operation.model_dump(mode="python")
        return QueryRequest.model_validate(payload)
    except Exception as exc:
        raise QueryRepairError("the corrected query is invalid") from exc


__all__ = ["QueryRepairError", "apply_query_scope_delta"]
