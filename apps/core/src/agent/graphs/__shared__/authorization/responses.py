"""Common response builders for authorization flows."""

from typing import Any, TypeVar

from apps.core.src.agent.graphs.__shared__.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)

StateT = TypeVar("StateT", bound=dict[str, Any])


async def build_session_expired_response(state: StateT) -> StateT:
    """Build response for expired/missing session."""
    synthesizer = get_synthesizer()
    context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
    response = await synthesizer.synthesize(context)

    return {
        **state,
        "response": response,
        "flow_state": "error",
    }


async def build_awaiting_pin_response(state: StateT) -> StateT:
    """Build response when waiting for PIN input."""
    return {
        **state,
        "response": "",  # No response needed, WhatsApp Flow handles PIN
        "flow_state": "authorizing",
    }


async def build_max_retries_response(state: StateT, error_msg: str) -> StateT:
    """Build response when max PIN retries exceeded."""
    synthesizer = get_synthesizer()
    context = build_response_context(
        ResponseIntent.MAX_ATTEMPTS_EXCEEDED,
        state,
        error_message=error_msg,
    )
    response = await synthesizer.synthesize(context)

    return {
        **state,
        "response": response,
        "flow_state": "error",
        "pin_verified": False,
        "pin_verification_error": error_msg,
    }


async def build_pin_failed_response(
    state: StateT,
    error_msg: str,
    retry_count: int,
) -> StateT:
    """Build response for PIN verification failure (with retries remaining)."""
    synthesizer = get_synthesizer()
    context = build_response_context(
        ResponseIntent.PIN_FAILED,
        state,
        error_message=error_msg,
    )
    response = await synthesizer.synthesize(context)

    return {
        **state,
        "response": response,
        "flow_state": "confirming",
        "pin_verified": False,
        "pin_verification_error": error_msg,
        "pin_retry_count": retry_count,
    }


async def build_authorization_error_response(
    state: StateT,
    error_msg: str,
    intent: ResponseIntent,
) -> StateT:
    """Build response for authorization error."""
    synthesizer = get_synthesizer()
    context = build_response_context(intent, state, error_message=error_msg)
    response = await synthesizer.synthesize(context)

    return {
        **state,
        "response": response,
        "flow_state": "error",
    }
