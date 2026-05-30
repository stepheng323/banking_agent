from __future__ import annotations

import time
import uuid
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referents.store import forget_stashed_referents
from apps.chat.src.agent.orchestrator.context.referents.task_memory import remember_referents_from_completed_task
from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.lifecycle.completed_transaction_frames import (
    TRANSACTION_TASK_TYPES,
    build_completed_transaction_frame,
)
from apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize_completed import (
    handle_completed_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.lifecycle.resume_prompt import (
    build_resume_prompt,
    has_live_resume_prompt_frame,
    is_resumable_stashed_session,
    stashed_session_id,
)
from banking.presentation.i18n.bridge import (
    render_cancelled_prompt,
    render_generic_capability_blocked,
)
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import (
    render_message,
)
from shared.utils.user_error import safe_user_error_message


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Final Step. Generate response and queue receipts."""
    outbox = list(state.outbox)
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    completed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.COMPLETED]
    failed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.FAILED]
    cancelled_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.CANCELLED]

    suppress_empty_fallback = False
    if completed_tasks:
        suppress_empty_fallback = await handle_completed_tasks(
            completed_tasks=completed_tasks,
            state=state,
            config=config,
            outbox=outbox,
        )

    for task in failed_tasks:
        if task.payload.get("capability_blocked"):
            message = task.payload.get("error") or render_generic_capability_blocked(locale)
            outbox.append({"type": "say", "text": message})
        elif task.payload.get("is_pending_mandate"):
            error_text = safe_user_error_message(task.payload.get("error"), task_type=task.type, locale=locale)
            outbox.append({"type": "say", "text": error_text})
        else:
            error_text = safe_user_error_message(task.payload.get("error"), task_type=task.type, locale=locale)
            outbox.append(
                {
                    "type": "say",
                    "text": render_message("orchestrator.finalize.failed_prefix", locale, {"error": error_text}),
                }
            )

    if cancelled_tasks:
        outbox.append({"type": "say", "text": render_cancelled_prompt(locale)})

    context_updates: dict[str, Any] = {}
    has_completed_non_transaction = any(task.type not in TRANSACTION_TASK_TYPES for task in completed_tasks)
    now_ts = int(time.time())

    for task in completed_tasks:
        remember_referents_from_completed_task(state, task)
    if completed_tasks:
        context_updates["referent_memory"] = state.referent_memory

    visible_completed_tasks = [task for task in completed_tasks if not task.payload.get("skip_finalize_summary")]
    completed_transaction_frame = build_completed_transaction_frame(
        visible_tasks=visible_completed_tasks,
        source_message_id=state.last_message_id,
    )
    if completed_transaction_frame is not None:
        OrchestratorContextManager().push_frame(state, completed_transaction_frame)
        context_updates["context_frames"] = state.context_frames
        context_updates["referent_memory"] = state.referent_memory

    resumable_stashed_sessions = [
        session
        for session in state.stashed_sessions
        if isinstance(session, dict)
        and is_resumable_stashed_session(cast(dict[str, Any], session), now_ts=now_ts)
    ]
    if len(resumable_stashed_sessions) != len(state.stashed_sessions):
        stale_stash_ids = {
            stash_id
            for session in state.stashed_sessions
            if isinstance(session, dict)
            and session not in resumable_stashed_sessions
            for stash_id in [stashed_session_id(session)]
            if stash_id
        }
        if stale_stash_ids:
            forget_stashed_referents(state, stale_stash_ids)
            context_updates["referent_memory"] = state.referent_memory
        context_updates["stashed_sessions"] = resumable_stashed_sessions

    if (
        resumable_stashed_sessions
        and has_completed_non_transaction
        and not has_live_resume_prompt_frame(state.context_frames)
    ):
        last_session = resumable_stashed_sessions[-1]
        intent = last_session.get("intent", render_message("orchestrator.session.default_intent", locale))
        resume_prompt = build_resume_prompt(last_session, locale=locale)

        if outbox and outbox[-1].get("type") == "say":
            outbox[-1]["text"] += f"\n\n{resume_prompt}"
        else:
            outbox.append({"type": "say", "text": resume_prompt})

        frame = ContextFrame(
            frame_id=str(uuid.uuid4()),
            frame_type=ContextFrameType.GENERIC,
            items=[
                ContextEntity(
                    entity_id="resumption_prompt",
                    label=f"Resume {intent}",
                    entity_type=EntityType.GENERIC,
                    data={
                        "intent": intent,
                        "resume_prompt": True,
                        "stash_id": stashed_session_id(last_session),
                    },
                )
            ],
            created_at_ts=int(time.time()),
            ttl_seconds=300,
        )
        current_frames = list(state.context_frames)
        current_frames.append(frame)
        context_updates["context_frames"] = current_frames

    return {
        "outbox": outbox,
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "pending_interrupt": None,
        "last_interrupt": None,
        "pin_verified": False,
        "last_callback": None,
        "session_stack": [],
        "active_domain": None,
        "suppress_empty_fallback": suppress_empty_fallback,
        **context_updates,
    }
