"""Helper for building ResponseContext from subgraph state."""

from typing import Any

from .context import ResponseContext
from .intent import ResponseIntent


def build_response_context(
    intent: ResponseIntent, state: dict[str, Any], **overrides
) -> ResponseContext:
    """Build ResponseContext from subgraph state.

    Extracts common fields from state and allows overrides.

    Args:
        intent: The response intent
        state: Subgraph state dictionary
        **overrides: Override any context field

    Returns:
        ResponseContext ready for synthesis
    """
    user_profile = state.get("user_profile") or {}
    user_name = user_profile.get("first_name") or user_profile.get("name")

    language = state.get("language") or "en"

    amount = state.get("amount")

    recipient_name = state.get("recipient_name")
    recipient_name = state.get("recipient_name")
    recipient_account = state.get("recipient_account")
    bank_name = state.get("recipient_bank_name")
    bank_code = state.get("recipient_bank_code")

    matched_beneficiary = state.get("matched_beneficiary")
    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        if not recipient_name:
            recipient_name = matched_beneficiary.get("account_name") or matched_beneficiary.get(
                "alias"
            )
        if not bank_name:
            bank_name = matched_beneficiary.get("bank_name")

    account_resolved = state.get("account_resolved")
    if account_resolved and isinstance(account_resolved, dict):
        if not recipient_name:
            recipient_name = account_resolved.get("account_name")

    phone_number = state.get("recipient_phone") or state.get("phone_number")
    network = state.get("network")
    data_plan = state.get("data_plan") or state.get("plan_name")

    selected_source = state.get("selected_source_account")
    source_account_name = None
    source_bank_name = None
    if selected_source and isinstance(selected_source, dict):
        source_account_name = selected_source.get("account_name")
        source_bank_name = selected_source.get("bank_name")

    balance = state.get("balance_available")

    error_message = None
    validation_errors = state.get("validation_errors") or []
    if validation_errors:
        error_message = (
            validation_errors[0]
            if isinstance(validation_errors[0], str)
            else str(validation_errors[0])
        )
    elif state.get("error"):
        error_message = state.get("error")
    elif state.get("pin_verification_error"):
        error_message = state.get("pin_verification_error")

    context = ResponseContext(
        intent=intent,
        user_name=user_name,
        language=language,
        amount=amount,
        recipient_name=recipient_name,
        recipient_account=recipient_account,
        bank_name=bank_name,
        bank_code=bank_code,
        phone_number=phone_number,
        network=network,
        data_plan=data_plan,
        source_account_name=source_account_name,
        source_bank_name=source_bank_name,
        balance=balance,
        error_message=error_message,
        transaction_id=state.get("transaction_id"),
        transaction_reference=state.get("idempotency_key"),
    )

    for key, value in overrides.items():
        if hasattr(context, key):
            setattr(context, key, value)

    return context


def build_clarification_context(
    intent: ResponseIntent, candidates: list[dict[str, Any]], state: dict[str, Any], **overrides
) -> ResponseContext:
    """Build ResponseContext for clarification responses.

    Args:
        intent: Clarification intent (e.g., CLARIFY_BENEFICIARY)
        candidates: List of candidate options
        state: Subgraph state
        **overrides: Override any context field

    Returns:
        ResponseContext with candidates
    """
    context = build_response_context(intent, state, **overrides)
    context.candidates = candidates
    return context
