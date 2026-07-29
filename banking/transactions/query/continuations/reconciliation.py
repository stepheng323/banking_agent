"""Deterministic cross-frame reconciliation for active query sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.continuations.repair import QueryRepairError, apply_query_scope_delta
from banking.transactions.query.contracts import SelectionPayload, SurfaceItemView
from banking.transactions.query.grounding.frames import resolve_query_frames
from banking.transactions.query.models.conversation import QueryScopeDelta
from banking.transactions.query.models.domain import QueryFrame, QueryIntent, QueryRequest
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    CounterpartyConcentrationSpec,
    SummarizeOperation,
)

ReconciliationOutcome = Literal["evidence_replay", "scope_explanation", "clarification", "expired"]


class _PeriodLike(Protocol):
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """A grounded answer explaining and optionally replaying an earlier result."""

    response: str
    outcome: ReconciliationOutcome
    source_frame_id: str | None = None
    source_query_request: QueryRequest | None = None
    matched_item_id: str | None = None
    evidence_payload: SelectionPayload | None = None
    corrected_query_request: QueryRequest | None = None
    difference_categories: tuple[str, ...] = ()


def _normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _tokens(value: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", _normalize(value)) if len(token) >= 3}


def _surface_item_matches_target(
    item: SurfaceItemView,
    *,
    target_text: str | None,
    target_amount: float | None,
) -> bool:
    """Match every supplied signal; never let a shared amount override text."""
    text_matches = True
    if target_text:
        target_tokens = _tokens(target_text)
        item_text = _normalize(item.label)
        item_tokens = _tokens(item_text)
        text_matches = bool(target_tokens) and (
            target_tokens.issubset(item_tokens) or _normalize(target_text) in item_text
        )

    amount_matches = True
    if target_amount is not None:
        amount_matches = item.amount is not None and abs(float(item.amount) - abs(target_amount)) < 0.01
    return text_matches and amount_matches


def _snapshot_item(snapshot: dict[str, Any]) -> SurfaceItemView | None:
    item_id = str(snapshot.get("id") or "").strip()
    label = str(snapshot.get("label") or "").strip()
    if not item_id or not label:
        return None
    raw_payload = snapshot.get("selection_payload")
    try:
        payload = SelectionPayload.model_validate(raw_payload)
    except Exception:
        # Older frame snapshots are display-only. They may be reconciled but
        # cannot authorize replay of an unavailable selector.
        payload = SelectionPayload(
            selection_kind="transaction",
            entity_type="transaction",
            entity_id=item_id,
            label=label,
        )
    amount = snapshot.get("amount")
    return SurfaceItemView(
        id=item_id,
        label=label,
        amount=float(amount) if isinstance(amount, (int, float)) else None,
        payload=payload,
        metadata=snapshot,
    )


def _candidate_frames(
    query_frames: list[QueryFrame] | None,
    referenced_frame_ids: list[str] | None,
) -> tuple[list[QueryFrame], bool]:
    if not query_frames:
        return [], bool(referenced_frame_ids)
    if referenced_frame_ids:
        frames = resolve_query_frames(query_frames, referenced_frame_ids)
        # A supplied but missing reference is an expiry, never permission to
        # reconcile against a different historical answer.
        resolved_ids = {frame.frame_id for frame in frames}
        return frames, not set(referenced_frame_ids).issubset(resolved_ids)
    return list(reversed(query_frames)), False


def _find_matches(
    *,
    frames: list[QueryFrame],
    target_text: str | None,
    target_amount: float | None,
) -> list[tuple[QueryFrame, SurfaceItemView]]:
    matches: list[tuple[QueryFrame, SurfaceItemView]] = []
    for frame in frames:
        for snapshot in frame.visible_items:
            if not isinstance(snapshot, dict):
                continue
            item = _snapshot_item(snapshot)
            if item is not None and _surface_item_matches_target(
                item,
                target_text=target_text,
                target_amount=target_amount,
            ):
                matches.append((frame, item))
    return matches


def _is_counterparty_concentration(query_request: QueryRequest | None) -> bool:
    return bool(
        query_request
        and query_request.intent == QueryIntent.INSIGHT
        and isinstance(query_request.operation, AnalyzeOperation)
        and isinstance(query_request.operation.analysis, CounterpartyConcentrationSpec)
    )


def _is_transfer_ranking(query_request: QueryRequest | None) -> bool:
    return bool(
        query_request
        and query_request.intent == QueryIntent.BENEFICIARY_SUMMARY
        and isinstance(query_request.operation, SummarizeOperation)
        and getattr(query_request.operation.summary, "dimension", None) == "counterparty"
    )


def _format_period(time_range: _PeriodLike | None, locale: str) -> str | None:
    if time_range is None:
        return None
    return render_message(
        "query.reconcile.period",
        locale,
        {"start": time_range.start.isoformat(), "end": time_range.end.isoformat()},
    )


def _contract_difference(
    current: QueryRequest | None,
    prior: QueryRequest | None,
    item: SurfaceItemView,
    locale: str,
) -> tuple[MessageKey, dict[str, Any], tuple[str, ...]]:
    if _is_counterparty_concentration(prior) and _is_transfer_ranking(current):
        return (
            "query.reconcile.scope_spending_vs_transfers",
            {"label": item.label, "amount": format_naira_compact(float(item.amount or 0), absolute=True)},
            ("measure", "inclusion_scope"),
        )
    current_period = _format_period(current.time_range if current else None, locale)
    prior_period = _format_period(prior.time_range if prior else None, locale)
    if current_period and prior_period and current_period != prior_period:
        return (
            "query.reconcile.scope_different_periods",
            {"label": item.label, "current_period": current_period, "found_period": prior_period},
            ("period",),
        )
    return "query.reconcile.found_in_earlier_answer", {"label": item.label}, ("surface",)


def _validated_evidence_payload(frame: QueryFrame, item: SurfaceItemView) -> SelectionPayload | None:
    payload = item.payload
    evidence = payload.insight_evidence
    if evidence is None or not isinstance(frame.query_request.operation, AnalyzeOperation):
        return None
    if frame.query_request.operation.analysis.insight_type != evidence.insight_type:
        return None
    return payload


async def reconcile_query_answer(
    *,
    session_query_request: QueryRequest | None,
    query_frames: list[QueryFrame] | None,
    target_text: str | None,
    target_amount: float | None,
    referenced_frame_ids: list[str] | None,
    correction_delta: QueryScopeDelta | None = None,
    locale: str = "en",
) -> ReconciliationResult:
    """Reconcile a challenge against one exact earlier visible result."""
    if not target_text and target_amount is None:
        return ReconciliationResult(
            response=render_message("query.clarify.missing_scope", locale),
            outcome="clarification",
        )

    frames, expired = _candidate_frames(query_frames, referenced_frame_ids)
    if expired:
        return ReconciliationResult(
            response=render_message("query.clarify.memory_unavailable", locale),
            outcome="expired",
        )
    matches = _find_matches(
        frames=frames,
        target_text=target_text,
        target_amount=target_amount,
    )
    if not matches:
        return ReconciliationResult(
            response=render_message("query.clarify.missing_scope", locale),
            outcome="clarification",
        )
    if len(matches) > 1:
        return ReconciliationResult(
            response=render_message("query.clarify.multiple_matches", locale, {"options": ""}),
            outcome="clarification",
        )

    frame, item = matches[0]
    key, data, categories = _contract_difference(session_query_request, frame.query_request, item, locale)
    payload = _validated_evidence_payload(frame, item)
    corrected_request = None
    if correction_delta is not None:
        try:
            corrected_request = apply_query_scope_delta(frame.query_request, correction_delta)
        except QueryRepairError:
            return ReconciliationResult(
                response=render_message("query.clarify.missing_scope", locale),
                outcome="clarification",
                source_frame_id=frame.frame_id,
                source_query_request=frame.query_request.model_copy(deep=True),
                matched_item_id=item.id,
            )
    return ReconciliationResult(
        response=render_message(key, locale, data),
        outcome="evidence_replay" if payload is not None else "scope_explanation",
        source_frame_id=frame.frame_id,
        source_query_request=frame.query_request.model_copy(deep=True),
        matched_item_id=item.id,
        evidence_payload=payload,
        corrected_query_request=corrected_request,
        difference_categories=categories,
    )


__all__ = ["ReconciliationOutcome", "ReconciliationResult", "reconcile_query_answer"]
