"""Fallback task construction for planner context-read requests."""

from typing import Literal

from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    CONTEXT_READ_ACCOUNT_SUBTYPES,
    CONTEXT_READ_BENEFICIARY_SUBTYPES,
)
from shared.types.planner import (
    AccountTaskParameters,
    BeneficiaryTaskParameters,
    PlannedTask,
    ResponseShape,
    make_planned_task,
)


def _response_shape_for_context_read_subtype(subtype: str) -> ResponseShape | None:
    if subtype in {"account_count", "beneficiary_count"}:
        return "fact_count"
    if subtype in {"account_linked_bank_existence_check", "beneficiary_existence_check"}:
        return "fact_bool"
    if subtype in {"linked_accounts_summary", "beneficiary_list", "beneficiary_name_match_preview"}:
        return "surface_list"
    if subtype in {"default_account_identity", "pending_mandate_explanation", "account_mandate_readiness_summary"}:
        return "fact_status"
    if subtype in {"flow_recap", "flow_missing_requirements"}:
        return "fact_recap"
    return None


def _build_context_read_fallback_task(
    subtype: str,
    message_text: str,
    *,
    account_action_override: str | None = None,
) -> PlannedTask | None:
    """Build a read-only worker task when context-read should not answer directly."""
    response_shape = _response_shape_for_context_read_subtype(subtype)
    parameters: AccountTaskParameters | BeneficiaryTaskParameters
    if subtype in CONTEXT_READ_ACCOUNT_SUBTYPES:
        parameters = AccountTaskParameters(response_shape=response_shape)
        action = account_action_override or "list_accounts"
        if action == "list":
            action = "list_accounts"
        if subtype == "account_count" and not account_action_override:
            action = "count"
        risk: Literal["READ_ONLY", "MUTATION"] = "READ_ONLY"
        if action in {"link", "unlink", "set_default"}:
            risk = "MUTATION"
        return make_planned_task(
            task_id="t1",
            action=action,
            executor="account",
            instruction=message_text,
            parameters=parameters,
            risk=risk,
        )

    if subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
        parameters = BeneficiaryTaskParameters(response_shape=response_shape)
        return make_planned_task(
            task_id="t1",
            action="list_beneficiaries",
            executor="beneficiary",
            instruction=message_text,
            parameters=parameters,
            risk="READ_ONLY",
        )

    return None


__all__ = ["_build_context_read_fallback_task"]
