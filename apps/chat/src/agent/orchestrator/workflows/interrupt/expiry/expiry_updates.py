import time
from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import build_cancellation_reset_updates
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.expiry.expiry_policy import (
    _pending_transaction_interrupt_expiry,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.expiry.expiry_stale_session import (
    _expired_transaction_message_targets_stale_session,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.presentation.i18n.renderer import render_message


async def _expired_transaction_interrupt_updates(
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    redis_client: Any | None,
    task_planner: Any,
    text: str,
) -> dict[str, Any] | None:
    expires_at = _pending_transaction_interrupt_expiry(interrupt, current_task_types)
    if expires_at is None or time.time() <= expires_at:
        return None

    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    should_notify = await _expired_transaction_message_targets_stale_session(
        state=state,
        interrupt=interrupt,
        current_task_types=current_task_types,
        task_planner=task_planner,
        text=text,
    )
    logger.info(
        "pending_transaction_interrupt_expired",
        kind=getattr(interrupt, "kind", None),
        task_ids=getattr(interrupt, "task_ids", None),
        expires_at=expires_at,
        notified=should_notify,
    )
    updates = {
        **reset_updates,
        "last_interrupt": interrupt,
    }
    if not should_notify:
        return updates

    locale = interrupt_state_view(state).current_locale
    response_text = render_message(
        "orchestrator.session.transaction_expired",
        locale,
        fallback_en=(
            "That transaction session has expired, so I can't continue it. Please start the transaction again."
        ),
    )
    return {
        **updates,
        "outbox": [{"type": "say", "text": response_text}],
        "final_response": response_text,
        "direct_path_triggered": True,
        "semantic_path_shape": "expired_transaction_session",
        "routing_owner": "guardrail",
        "routing_decision": "expired_transaction_session",
        "routing_target_domain": None,
        "routing_mode": "expired",
    }


__all__ = ["_expired_transaction_interrupt_updates"]
