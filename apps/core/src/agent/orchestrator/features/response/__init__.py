"""Response Synthesizer module - Unified response generation.

Usage:
    from apps.core.src.agent.orchestrator.features.response import (
        ResponseIntent,
        ResponseContext,
        ResponseSynthesizer,
        build_response_context,
        build_clarification_context,
    )

    # Build context from state
    context = build_response_context(
        intent=ResponseIntent.CONFIRM_TRANSFER,
        state=transfer_state,
    )

    # Generate response
    synthesizer = ResponseSynthesizer()
    response = await synthesizer.synthesize(context)
"""

from .builder import build_clarification_context, build_response_context
from .context import ResponseContext
from .intent import ResponseIntent
from .synthesizer import ResponseSynthesizer, get_synthesizer

__all__ = [
    "ResponseIntent",
    "ResponseContext",
    "ResponseSynthesizer",
    "get_synthesizer",
    "build_response_context",
    "build_clarification_context",
]
