"""Saved-beneficiary grounding for recipient-specific transaction queries."""

from __future__ import annotations

import re
from typing import Any

from banking.transactions.query.continuations.clarification_state import clarification_candidate
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.extraction import ClarificationCandidate
from banking.transactions.query.models.operations import (
    NamedCounterparty,
    QueryRequest,
    RetrieveOperation,
    SummarizeOperation,
)


def _normal(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _candidate_values(row: dict[str, Any]) -> tuple[str, str]:
    return _normal(row.get("alias")), _normal(row.get("account_name"))


def _is_transfer_beneficiary(row: dict[str, Any]) -> bool:
    kind = str(row.get("beneficiary_type") or "transfer").strip().lower()
    return kind in {"", "transfer"}


def _matches_requested_recipient(requested: str, row: dict[str, Any]) -> bool:
    alias, account_name = _candidate_values(row)
    if requested in {alias, account_name}:
        return True
    if not requested or len(requested) < 3:
        return False
    requested_tokens = set(requested.split())
    return requested_tokens.issubset(set(alias.split())) or requested_tokens.issubset(set(account_name.split()))


def _label(row: dict[str, Any]) -> str:
    alias = str(row.get("alias") or "").strip()
    account_name = str(row.get("account_name") or alias or "Recipient").strip()
    bank = str(row.get("bank_name") or "").strip()
    account = "".join(char for char in str(row.get("account_number") or "") if char.isdigit())
    suffix = f" · ···{account[-4:]}" if len(account) >= 4 else ""
    identity = f"{account_name} ({alias})" if alias and alias.casefold() != account_name.casefold() else account_name
    return " · ".join(part for part in (identity, bank) if part) + suffix


def recipient_clarification_candidates(
    request: QueryRequest,
    beneficiaries: list[Any],
) -> list[ClarificationCandidate]:
    """Return saved-recipient candidates only for an ambiguous outgoing query.

    Incoming counterparties are not beneficiaries, and arbitrary counterparty
    text remains a normal transaction search.  This guard is intentionally
    based on the compiled contract rather than raw message wording.
    """

    operation = request.operation
    scope = operation.scope if isinstance(operation, (RetrieveOperation, SummarizeOperation)) else None
    predicate = scope.predicate if scope is not None else None
    counterparty = predicate.counterparty if predicate is not None else None
    reference = counterparty.reference if counterparty is not None else None
    if predicate is None or predicate.direction != "debit" or not isinstance(reference, NamedCounterparty):
        return []

    requested = _normal(reference.name)
    if not requested:
        return []
    matches = [
        row
        for row in beneficiaries
        if isinstance(row, dict) and _is_transfer_beneficiary(row) and _matches_requested_recipient(requested, row)
    ]
    # Unique saved identities improve grounding; an unmatched raw name should
    # retain existing query behavior.  Only broad multi-match references need
    # a clarification.
    if len(matches) < 2:
        return []

    candidates: list[ClarificationCandidate] = []
    seen_ids: set[str] = set()
    for row in matches:
        identifier = str(row.get("id") or row.get("beneficiary_id") or "").strip()
        account_name = str(row.get("account_name") or row.get("alias") or "").strip()
        if not identifier or not account_name or identifier in seen_ids:
            continue
        seen_ids.add(identifier)
        candidates.append(
            clarification_candidate(
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id=identifier,
                    label=_label(row),
                    filters_patch={"counterparty": [account_name]},
                ),
                label=_label(row),
            )
        )
    return candidates[:5]


def ground_unique_saved_recipient(
    request: QueryRequest,
    beneficiaries: list[Any],
) -> QueryRequest:
    """Replace a unique saved alias with its canonical recipient name."""

    operation = request.operation
    scope = operation.scope if isinstance(operation, (RetrieveOperation, SummarizeOperation)) else None
    predicate = scope.predicate if scope is not None else None
    counterparty = predicate.counterparty if predicate is not None else None
    reference = counterparty.reference if counterparty is not None else None
    if predicate is None or predicate.direction != "debit" or not isinstance(reference, NamedCounterparty):
        return request
    requested = _normal(reference.name)
    matches = [
        row
        for row in beneficiaries
        if isinstance(row, dict) and _is_transfer_beneficiary(row) and _matches_requested_recipient(requested, row)
    ]
    if len(matches) != 1:
        return request
    canonical_name = str(matches[0].get("account_name") or matches[0].get("alias") or "").strip()
    if not canonical_name:
        return request
    if counterparty is None or scope is None:
        return request
    grounded_reference = reference.model_copy(update={"name": canonical_name})
    grounded_counterparty = counterparty.model_copy(update={"reference": grounded_reference})
    grounded_predicate = predicate.model_copy(update={"counterparty": grounded_counterparty})
    grounded_scope = scope.model_copy(update={"predicate": grounded_predicate})
    grounded_operation = operation.model_copy(update={"scope": grounded_scope})
    return request.model_copy(update={"operation": grounded_operation})


__all__ = ["ground_unique_saved_recipient", "recipient_clarification_candidates"]
