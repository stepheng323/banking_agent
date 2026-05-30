from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.referents.store import forget_stashed_referents
from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.task_handlers.context_frames import clear_resume_prompt_frames
from apps.chat.src.agent.orchestrator.task_handlers.runtime import ExecutionContext, _state_locale
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_orchestrator_task(task: Any, task_id: str, ctx: ExecutionContext) -> None:
    """Handle orchestrator tasks such as accepting or dismissing a stashed session."""
    del task_id
    action = task.payload.get("action")
    locale = _state_locale(ctx.state)
    if action not in {"resume_session", "dismiss_resume_session"}:
        return

    if not ctx.state.stashed_sessions:
        no_stash_message = render_message("orchestrator.session.no_stashed", locale)
        ctx.agg.say(no_stash_message)
        task.stage = TaskStage.FAILED
        task.payload["error"] = no_stash_message
        return

    last_session = ctx.state.stashed_sessions[-1]
    remaining_stash = ctx.state.stashed_sessions[:-1]
    intent = str(last_session.get("intent", render_message("orchestrator.session.default_intent", locale)))
    ctx.agg.updates["stashed_sessions"] = remaining_stash
    ctx.agg.updates["context_frames"] = clear_resume_prompt_frames(ctx.state.context_frames)
    stash_id = str(last_session.get("stash_id") or "").strip()
    if stash_id:
        forget_stashed_referents(ctx.state, {stash_id})
        ctx.agg.updates["referent_memory"] = ctx.state.referent_memory

    if action == "resume_session":
        p_interrupt = last_session.get("pending_interrupt")
        logger.info("resuming_session", intent=intent, has_interrupt=bool(p_interrupt))
        restored_tasks = cast(dict[str, Any], last_session["tasks"])

        ctx.agg.updates["tasks"] = restored_tasks
        ctx.agg.updates["waves"] = last_session["waves"]
        ctx.agg.updates["current_wave_index"] = last_session["current_wave_index"]
        ctx.agg.updates["pending_interrupt"] = None
        ctx.agg.updates["last_interrupt"] = p_interrupt
        ctx.agg.updates["last_message_text"] = None
        task.stage = TaskStage.COMPLETED
        return

    logger.info("resume_session_declined", intent=intent)
    ctx.agg.say(render_message("orchestrator.session.resume_declined", locale))
    task.stage = TaskStage.COMPLETED
