"""Lifecycle finalization reducers."""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referents.store import forget_stashed_referents
from apps.chat.src.agent.orchestrator.context.referents.task_memory import remember_referents_from_completed_task
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.lifecycle.accumulator import FinalizeAccumulator
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
from apps.chat.src.agent.orchestrator.workflows.lifecycle.runtime import FinalizeRuntime
from banking.presentation.i18n.bridge import (
    render_cancelled_prompt,
    render_generic_capability_blocked,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.user_error import safe_user_error_message


async def _reduce_completed_task_outputs(runtime: FinalizeRuntime, acc: FinalizeAccumulator) -> None:
    if not runtime.completed_tasks:
        return
    suppress_empty_fallback = await handle_completed_tasks(
        completed_tasks=runtime.completed_tasks,
        state_view=runtime.state_view,
        dependencies=runtime.dependencies,
        outbox=acc.outbox,
    )
    acc.set_suppress_empty_fallback(suppress_empty_fallback)


def _reduce_failed_task_outputs(runtime: FinalizeRuntime, acc: FinalizeAccumulator) -> None:
    for task in runtime.failed_tasks:
        if task.payload.get("capability_blocked"):
            message = task.payload.get("error") or render_generic_capability_blocked(runtime.locale)
            acc.append_outbox({"type": "say", "text": message})
        elif task.payload.get("is_pending_mandate"):
            error_text = safe_user_error_message(task.payload.get("error"), task_type=task.type, locale=runtime.locale)
            acc.append_outbox({"type": "say", "text": error_text})
        else:
            error_text = safe_user_error_message(task.payload.get("error"), task_type=task.type, locale=runtime.locale)
            acc.append_outbox(
                {
                    "type": "say",
                    "text": render_message(
                        "orchestrator.finalize.failed_prefix",
                        runtime.locale,
                        {"error": error_text},
                    ),
                }
            )


def _reduce_cancelled_task_outputs(runtime: FinalizeRuntime, acc: FinalizeAccumulator) -> None:
    if runtime.cancelled_tasks:
        acc.append_outbox({"type": "say", "text": render_cancelled_prompt(runtime.locale)})


def _reduce_completed_referents(runtime: FinalizeRuntime, acc: FinalizeAccumulator) -> None:
    for task in runtime.completed_tasks:
        remember_referents_from_completed_task(runtime.state, task)
    if runtime.completed_tasks:
        acc.set_referent_memory(runtime.state_view.referent_memory)


def _visible_completed_tasks(completed_tasks: list[TaskSpec]) -> list[TaskSpec]:
    return [task for task in completed_tasks if not task.payload.get("skip_finalize_summary")]


def _reduce_completed_transaction_frame(runtime: FinalizeRuntime, acc: FinalizeAccumulator) -> None:
    completed_transaction_frame = build_completed_transaction_frame(
        visible_tasks=_visible_completed_tasks(runtime.completed_tasks),
        source_message_id=runtime.state_view.last_message_id,
    )
    if completed_transaction_frame is None:
        return
    OrchestratorContextManager().push_frame(runtime.state, completed_transaction_frame)
    acc.set_context_frames(runtime.state_view.context_frames)
    acc.set_referent_memory(runtime.state_view.referent_memory)


def _resumable_stashed_sessions(runtime: FinalizeRuntime, *, now_ts: int) -> list[dict[str, Any]]:
    return [
        session
        for session in runtime.state_view.stashed_sessions
        if isinstance(session, dict) and is_resumable_stashed_session(cast(dict[str, Any], session), now_ts=now_ts)
    ]


def _reduce_stale_stashed_sessions(
    runtime: FinalizeRuntime,
    acc: FinalizeAccumulator,
    *,
    resumable_sessions: list[dict[str, Any]],
) -> None:
    if len(resumable_sessions) == len(runtime.state_view.stashed_sessions):
        return

    stale_stash_ids = {
        stash_id
        for session in runtime.state_view.stashed_sessions
        if isinstance(session, dict) and session not in resumable_sessions
        for stash_id in [stashed_session_id(session)]
        if stash_id
    }
    if stale_stash_ids:
        forget_stashed_referents(runtime.state, stale_stash_ids)
        acc.set_referent_memory(runtime.state_view.referent_memory)
    acc.set_stashed_sessions(resumable_sessions)


def _completed_non_transaction(completed_tasks: list[TaskSpec]) -> bool:
    return any(task.type not in TRANSACTION_TASK_TYPES for task in completed_tasks)


def _build_resume_prompt_frame(session: dict[str, Any], *, intent: str) -> ContextFrame:
    return ContextFrame(
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
                    "stash_id": stashed_session_id(session),
                },
            )
        ],
        created_at_ts=int(time.time()),
        ttl_seconds=300,
    )


def _reduce_resume_prompt(
    runtime: FinalizeRuntime,
    acc: FinalizeAccumulator,
    *,
    resumable_sessions: list[dict[str, Any]],
) -> None:
    if not resumable_sessions:
        return
    if not _completed_non_transaction(runtime.completed_tasks):
        return
    if has_live_resume_prompt_frame(runtime.state_view.context_frames):
        return

    last_session = resumable_sessions[-1]
    intent = last_session.get("intent", render_message("orchestrator.session.default_intent", runtime.locale))
    resume_prompt = build_resume_prompt(last_session, locale=runtime.locale)

    if acc.outbox and acc.outbox[-1].get("type") == "say":
        acc.outbox[-1]["text"] += f"\n\n{resume_prompt}"
    else:
        acc.append_outbox({"type": "say", "text": resume_prompt})

    current_frames = runtime.state_view.context_frames
    current_frames.append(_build_resume_prompt_frame(last_session, intent=intent))
    acc.set_context_frames(current_frames)


async def reduce_finalize_runtime(runtime: FinalizeRuntime) -> dict[str, Any]:
    """Reduce a finalize runtime into LangGraph state updates."""
    acc = FinalizeAccumulator(outbox=runtime.outbox)

    await _reduce_completed_task_outputs(runtime, acc)
    _reduce_failed_task_outputs(runtime, acc)
    _reduce_cancelled_task_outputs(runtime, acc)

    _reduce_completed_referents(runtime, acc)
    _reduce_completed_transaction_frame(runtime, acc)

    resumable_sessions = _resumable_stashed_sessions(runtime, now_ts=int(time.time()))
    _reduce_stale_stashed_sessions(runtime, acc, resumable_sessions=resumable_sessions)
    _reduce_resume_prompt(runtime, acc, resumable_sessions=resumable_sessions)

    return acc.to_updates()


__all__ = ["reduce_finalize_runtime"]
