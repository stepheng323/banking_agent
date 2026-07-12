from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import ConversationResponseMode
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext


async def _build_bounded_conversational_reply(
    ctx: GateContext,
    locale: str,
    *,
    mode: ConversationResponseMode = ConversationResponseMode.CASUAL,
    extra_user_ctx: dict[str, object] | None = None,
) -> str | None:
    """Generate a bounded conversational reply using the ConversationResponder."""
    if ctx.conversation_responder is None:
        return None
    try:
        return await ctx.conversation_responder.generate_reply(
            ctx.message_text,
            {
                **ctx.state_view.loaded_context_or_empty,
                "language": locale,
                **(extra_user_ctx or {}),
            },
            mode=mode,
        )
    except Exception as exc:
        import structlog

        structlog.get_logger(__name__).warning("gate_conversational_responder_failed", error=str(exc))
        return None
