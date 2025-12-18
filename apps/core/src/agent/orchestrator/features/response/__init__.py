"""Response Synthesizer module - Unified response generation.

Usage:
    from apps.core.src.agent.orchestrator.features.response import (
        ResponseIntent,
        ResponseContext,
        ResponseSynthesizer,
        build_response_context,
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

from .intent import ResponseIntent
from .context import ResponseContext
from .synthesizer import ResponseSynthesizer, get_synthesizer
from .builder import build_response_context

__all__ = [
    "ResponseIntent",
    "ResponseContext",
    "ResponseSynthesizer",
    "get_synthesizer",
    "build_response_context",
]
