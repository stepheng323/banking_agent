"""Resolution of grounded post-answer query corrections."""

from __future__ import annotations

import re
from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.repair import QueryRepairError, apply_query_scope_delta
from banking.transactions.query.models.conversation import (
    PendingInterpretationProposal,
    QueryExecutionContract,
    QueryInterpretationProposal,
    QueryScopeDelta,
    QueryTurnPlan,
    SingleQueryExecution,
)
from banking.transactions.query.models.operations import QueryRequest

_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1}
_CANCELS = {"cancel", "neither", "none", "none of them"}
_AUTO_CONFIDENCE = 0.75
_LOW_CONFIDENCE = 0.45


def _proposal_label(delta: QueryScopeDelta, index: int) -> str:
    """Use stable semantic labels; presentation never relies on model prose."""
    fields = [
        name
        for name, value in (
            ("period", delta.period_mutation),
            ("account", delta.account_mutation),
            ("recipient", delta.counterparty_mutation),
            ("direction", delta.direction_mutation),
            ("category", delta.category_mutation),
            ("status", delta.status_mutation),
            ("amount", delta.amount_mutation),
            ("measure", delta.measure),
            ("grouping", delta.dimension),
        )
        if value is not None
    ]
    return ", ".join(fields) if fields else f"interpretation {index}"


def _proposal_updates(
    *,
    request: QueryRequest,
    primary: QueryScopeDelta,
    alternate: QueryScopeDelta,
    confidence: float,
    locale: str,
    session: dict[str, Any],
    source_frame_id: str | None,
    turn_id: str | None,
    execution_contract: object | None,
    target_step_id: str | None,
) -> dict[str, Any]:
    try:
        primary_request = apply_query_scope_delta(request, primary)
        alternate_request = apply_query_scope_delta(request, alternate)
        primary_contract = _replace_execution_request(execution_contract, target_step_id, primary_request)
        alternate_contract = _replace_execution_request(execution_contract, target_step_id, alternate_request)
    except QueryRepairError:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.missing_scope", locale),
            "session_active": True,
            "flow_state": "parsing",
        }
    if primary_contract == alternate_contract:
        return _execute_updates(
            primary_request,
            execution_contract=primary_contract,
            source_frame_id=source_frame_id,
        )

    proposals = [
        QueryInterpretationProposal(
            proposal_id="option_1",
            contract=primary_contract,
            source_frame_id=source_frame_id,
            difference_fields=_proposal_label(primary, 1).split(", "),
            confidence=confidence,
        ),
        QueryInterpretationProposal(
            proposal_id="option_2",
            contract=alternate_contract,
            source_frame_id=source_frame_id,
            difference_fields=_proposal_label(alternate, 2).split(", "),
            confidence=max(0.0, confidence - 0.01),
        ),
    ]
    pending = PendingInterpretationProposal(
        source_frame_id=source_frame_id,
        proposals=proposals,
        created_turn_id=turn_id,
    )
    options = "\n".join(
        f"{index}. {_proposal_label(delta, index)}" for index, delta in enumerate((primary, alternate), 1)
    )
    return {
        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
        "response": render_message("query.clarify.multiple_matches", locale, {"options": options}),
        "session_active": True,
        "flow_state": "parsing",
        "pending_query_input": pending.model_dump(mode="json"),
        "query_frames": session.get("query_frames"),
        "query_request": request,
    }


def _replace_execution_request(
    raw_contract: object | None,
    target_step_id: str | None,
    request: QueryRequest,
) -> QueryExecutionContract:
    if raw_contract is None:
        return SingleQueryExecution(request=request)
    try:
        plan = raw_contract if isinstance(raw_contract, QueryTurnPlan) else QueryTurnPlan.model_validate(raw_contract)
    except Exception:
        return SingleQueryExecution(request=request)
    if target_step_id is None:
        raise QueryRepairError("a composite repair requires a target section")
    if not any(step.step_id == target_step_id for step in plan.steps):
        raise QueryRepairError("the selected composite section is unavailable")
    return plan.model_copy(
        update={
            "steps": [
                step.model_copy(update={"request": request}) if step.step_id == target_step_id else step
                for step in plan.steps
            ]
        }
    )


def _execute_updates(
    request: QueryRequest,
    *,
    execution_contract: QueryExecutionContract,
    source_frame_id: str | None,
) -> dict[str, Any]:
    return {
        "query_request": request,
        "execution_contract": execution_contract,
        "execute_query_plan": isinstance(execution_contract, QueryTurnPlan),
        "flow_state": "executing",
        "session_active": True,
        "pending_query_input": None,
        "current_page": 0,
        "show_expanded": False,
        "continuation_type": "repair",
        "repair_source_frame_id": source_frame_id,
        "resolver_message": None,
    }


def resolve_repair(
    *,
    request: QueryRequest | None,
    primary: QueryScopeDelta | None,
    alternate: QueryScopeDelta | None,
    confidence: float | None,
    locale: str,
    session: dict[str, Any],
    source_frame_id: str | None,
    turn_id: str | None,
    execution_contract: object | None = None,
    target_step_id: str | None = None,
) -> dict[str, Any]:
    """Apply an unambiguous correction or retain two validated choices."""
    if request is None or primary is None:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.missing_scope", locale),
            "session_active": True,
            "flow_state": "parsing",
        }
    score = float(confidence or 0.0)
    if alternate is not None:
        return _proposal_updates(
            request=request,
            primary=primary,
            alternate=alternate,
            confidence=score,
            locale=locale,
            session=session,
            source_frame_id=source_frame_id,
            turn_id=turn_id,
            execution_contract=execution_contract,
            target_step_id=target_step_id,
        )
    if score < _LOW_CONFIDENCE:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.missing_scope", locale),
            "session_active": True,
            "flow_state": "parsing",
        }
    try:
        repaired = apply_query_scope_delta(request, primary)
        repaired_execution = _replace_execution_request(execution_contract, target_step_id, repaired)
    except QueryRepairError:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.missing_scope", locale),
            "session_active": True,
            "flow_state": "parsing",
        }
    if score < _AUTO_CONFIDENCE:
        # A single low-confidence reading has no grounded alternative. Ask for
        # the field rather than silently running a possibly wrong query.
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.missing_scope", locale),
            "session_active": True,
            "flow_state": "parsing",
        }
    return _execute_updates(
        repaired,
        execution_contract=repaired_execution,
        source_frame_id=source_frame_id,
    )


def resolve_pending_proposal(
    pending: PendingInterpretationProposal,
    message: str,
    *,
    locale: str,
    session: dict[str, Any],
) -> dict[str, Any] | None:
    normalized = " ".join(message.casefold().split())
    if normalized in _CANCELS:
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": render_message("query.clarify.cancelled", locale),
            "session_active": False,
            "flow_state": "complete",
            "pending_query_input": None,
        }
    index = None
    if normalized.isdigit():
        index = int(normalized) - 1
    else:
        for token, candidate_index in _ORDINALS.items():
            if re.search(rf"\b{re.escape(token)}\b", normalized):
                index = candidate_index
                break
    if index is not None and 0 <= index < len(pending.proposals):
        contract = pending.proposals[index].contract
        if isinstance(contract, SingleQueryExecution):
            return _execute_updates(
                contract.request,
                execution_contract=contract,
                source_frame_id=pending.source_frame_id,
            )
        if isinstance(contract, QueryTurnPlan):
            primary = next((step for step in contract.steps if step.role == "primary"), None)
            if primary is not None:
                return _execute_updates(
                    primary.request,
                    execution_contract=contract,
                    source_frame_id=pending.source_frame_id,
                )
    attempts = pending.attempt_count + 1
    if attempts >= 2:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.exhausted", locale),
            "session_active": True,
            "flow_state": "parsing",
            "pending_query_input": None,
        }
    return {
        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
        "response": render_message("query.clarify.reply_number_or_rephrase", locale),
        "session_active": True,
        "flow_state": "parsing",
        "pending_query_input": pending.model_copy(update={"attempt_count": attempts}).model_dump(mode="json"),
    }


__all__ = ["resolve_pending_proposal", "resolve_repair"]
