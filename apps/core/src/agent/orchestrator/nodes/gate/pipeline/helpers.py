
from apps.core.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext


async def _build_bounded_conversational_reply(ctx: GateContext, locale: str) -> str | None:
    """Generate a bounded conversational reply using the ConversationResponder."""
    if ctx.conversation_responder is None:
        return None
    try:
        return await ctx.conversation_responder.generate_reply(
            ctx.state.phone_number,
            ctx.message_text,
            {
                **(ctx.state.loaded_context or {}),
                "language": locale,
            },
            intent="non_banking_conversational",
        )
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).warning("gate_conversational_responder_failed", error=str(exc))
        return None
