from typing import Any

from apps.chat.src.agent.orchestrator.gibberish import looks_like_gibberish, render_gibberish_prompt
from apps.chat.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    clear_query_session,
    has_cancelable_state,
    is_explicit_cancel_message,
)
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _has_pending_mandate_without_ready_accounts,
    _locale_update,
    _pending_interrupt_task_types,
    _resolve_explicit_language_switch,
    _route_observability_updates,
)
from shared.i18n.bridge import render_locale_switched

# Explicit imports from gate.py helpers
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.services.confirmation_decision import classify_confirmation_reply_sync
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


async def _stage_gibberish_filter(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic fast-path for obvious gibberish/spam before semantic routing."""
    if looks_like_gibberish(ctx.message_text):
        logger.info("gate_gibberish_filtered")
        return {
            **ctx.gate_updates,
            "direct_path_triggered": True,
            "final_response": render_gibberish_prompt(ctx.current_locale),
            "semantic_path_shape": "gibberish_direct",
            **_route_observability_updates(owner="guardrail", decision="gibberish_filtered"),
        }
    return None


async def _stage_expired_pin(ctx: GateContext) -> dict[str, Any] | None:
    """PIN verified but no active session (checkpoint was cleaned)."""
    if not ctx.state.pin_verified or ctx.live_pending_interrupt:
        return None
    ctx.gate_updates["pin_verified"] = False
    if not _stale_pin_message_targets_missing_session(ctx):
        return None
    logger.warning("gate_pin_verified_no_session", reason="checkpoint_cleaned")
    return {
        **ctx.gate_updates,
        "direct_path_triggered": True,
        "final_response": render_message(
            "orchestrator.session.transaction_expired",
            ctx.current_locale,
            fallback_en=(
                "That transaction session has expired, so I can't continue it. "
                "Please start the transaction again."
            ),
        ),
        **_route_observability_updates(owner="guardrail", decision="expired_pin_session"),
    }


def _stale_pin_message_targets_missing_session(ctx: GateContext) -> bool:
    callback = ctx.state.last_callback if isinstance(ctx.state.last_callback, dict) else {}
    if callback.get("pin_verified"):
        return True

    transaction_decision = classify_confirmation_reply_sync(
        ctx.message_text,
        prompt_kind="transaction_confirmation",
        locale=ctx.current_locale,
    )
    if transaction_decision.action in {"approve", "reject", "modify"}:
        return True

    resume_decision = classify_confirmation_reply_sync(
        ctx.message_text,
        prompt_kind="resume_prompt",
        locale=ctx.current_locale,
    )
    return resume_decision.action in {"approve", "reject"}
