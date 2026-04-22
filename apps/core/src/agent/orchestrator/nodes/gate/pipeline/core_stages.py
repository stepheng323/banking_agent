from typing import Any

from apps.core.src.agent.orchestrator.nodes.gate.runner import (
    _has_pending_mandate_without_ready_accounts,
    _locale_update,
    _pending_interrupt_task_types,
    _resolve_explicit_language_switch,
    _route_observability_updates,
)

from apps.core.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    clear_query_session,
    has_cancelable_state,
    is_explicit_cancel_message,
)
from apps.core.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from shared.i18n.bridge import render_locale_switched

# Explicit imports from gate.py helpers
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _stage_stale_interrupt_cleanup(ctx: GateContext) -> None:
    """Sync stage: clear dead interrupts that have no live tasks."""
    if ctx.state.pending_interrupt is not None and not ctx.live_pending_interrupt:
        logger.info(
            "interrupt_router_skipped_no_live_flow",
            kind=getattr(ctx.state.pending_interrupt, "kind", None),
            task_ids=getattr(ctx.state.pending_interrupt, "task_ids", None),
            current_task_types=sorted(_pending_interrupt_task_types(ctx.state)),
        )
        ctx.gate_updates["pending_interrupt"] = None


async def _stage_language_switch(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic explicit language switch command."""
    requested_locale = _resolve_explicit_language_switch(ctx.message_text)
    if requested_locale is None:
        return None
    if ctx.redis_client:
        resolved = await LocaleManager.set_locale(
            ctx.state.phone_number,
            requested_locale,
            source="user_command",
        )
        next_locale = resolved.value
    else:
        next_locale = requested_locale
    logger.info("gate_explicit_language_switch", locale=next_locale)
    return {
        **ctx.gate_updates,
        "direct_path_triggered": True,
        "final_response": render_locale_switched(next_locale),
        **_locale_update(ctx.state, next_locale),
        **_route_observability_updates(owner="guardrail", decision="language_switch"),
    }


async def _stage_cancel(ctx: GateContext) -> dict[str, Any] | None:
    """Explicit cancel handling."""
    if not is_explicit_cancel_message(ctx.message_text):
        return None
    if has_cancelable_state(ctx.state):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
        return {
            **ctx.gate_updates,
            **cleanup_updates,
            "direct_path_triggered": True,
            "final_response": cancelled_message(ctx.state, ctx.current_locale),
            **_route_observability_updates(owner="guardrail", decision="cancel"),
        }

    await ctx.ensure_query_session()

    if (
        isinstance(ctx.query_session_snapshot, dict)
        and ctx.query_session_snapshot.get("session_active")
        and ctx.query_session_snapshot.get("pending_clarification")
    ):
        await clear_query_session(ctx.redis_client, ctx.state.phone_number)
        return {
            **ctx.gate_updates,
            "direct_path_triggered": True,
            "final_response": render_message("query.session.goodbye", ctx.current_locale),
            **_route_observability_updates(owner="guardrail", decision="cancel"),
        }
    if _has_pending_mandate_without_ready_accounts(ctx.state.loaded_context):
        logger.info("gate_pending_mandate_notice_dismissed")
        return {
            **ctx.gate_updates,
            "direct_path_triggered": True,
            "final_response": cancelled_message(ctx.state, ctx.current_locale),
            **_route_observability_updates(owner="guardrail", decision="cancel_pending_mandate_notice"),
        }
    return {
        **ctx.gate_updates,
        "direct_path_triggered": True,
        "final_response": clarify_message(ctx.state, ctx.current_locale),
        **_route_observability_updates(owner="guardrail", decision="cancel"),
    }


async def _stage_expired_pin(ctx: GateContext) -> dict[str, Any] | None:
    """PIN verified but no active session (checkpoint was cleaned)."""
    if not ctx.state.pin_verified or ctx.live_pending_interrupt:
        return None
    logger.warning("gate_pin_verified_no_session", reason="checkpoint_cleaned")
    return {
        **ctx.gate_updates,
        "direct_path_triggered": True,
        "final_response": render_message(
            "orchestrator.session.expired_pin",
            ctx.current_locale,
            fallback_en="Your transaction session has expired. Please start a new transaction.",
        ),
        **_route_observability_updates(owner="guardrail", decision="expired_pin_session"),
    }
