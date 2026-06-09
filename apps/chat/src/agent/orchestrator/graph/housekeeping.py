"""Background housekeeping for orchestrator graph threads."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Callable
from typing import Any

from apps.chat.src.agent.orchestrator.context.referents.store import referent_memory_ttl_seconds
from apps.chat.src.agent.orchestrator.workflows.lifecycle.resume_prompt import STASH_RESUME_TTL_SECONDS
from shared.config.settings import settings
from shared.utils.async_helpers import create_background_task
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorHousekeeping:
    """Applies checkpoint TTLs and removes idle graph threads."""

    def __init__(
        self,
        *,
        redis_client: Any,
        checkpointer: Any,
        log_latency_span: Callable[..., None],
    ) -> None:
        self.redis_client = redis_client
        self.checkpointer = checkpointer
        self._log_latency_span = log_latency_span
        self._semaphore = asyncio.Semaphore(max(1, settings.async_housekeeping_max_concurrency))

    async def run(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        phone_number: str,
        path_label: str,
    ) -> None:
        create_background_task(
            self.run_with_retries(
                thread_id=thread_id,
                state=state,
                phone_number=phone_number,
                path_label=path_label,
            ),
            task_name="orchestrator_housekeeping",
        )

    async def run_with_retries(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        phone_number: str,
        path_label: str,
    ) -> None:
        max_attempts = max(1, settings.async_housekeeping_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            async with self._semaphore:
                cleanup_start = time.perf_counter()
                cleanup_ok = await self.cleanup_if_idle(thread_id, state)
                cleanup_duration = (time.perf_counter() - cleanup_start) * 1000
                self._log_latency_span(
                    span="cleanup",
                    duration_ms=cleanup_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )

                ttl_start = time.perf_counter()
                ttl_ok = await self.maybe_apply_session_ttl(thread_id, state)
                ttl_duration = (time.perf_counter() - ttl_start) * 1000
                self._log_latency_span(
                    span="ttl_apply",
                    duration_ms=ttl_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )

            if cleanup_ok and ttl_ok:
                return
            if attempt >= max_attempts:
                logger.warning(
                    "housekeeping_retry_exhausted",
                    thread_id=thread_id,
                    attempts=attempt,
                    cleanup_ok=cleanup_ok,
                    ttl_ok=ttl_ok,
                )
                return
            backoff = (settings.async_housekeeping_retry_base_ms * attempt) / 1000.0
            await asyncio.sleep(backoff + random.uniform(0.01, 0.09))

    async def maybe_apply_session_ttl(self, thread_id: str, state: dict[str, Any] | None = None) -> bool:
        chat_ok = await self.apply_chat_history_ttl(thread_id)
        ttl = self.checkpoint_ttl_seconds(state or {})
        if ttl <= 0:
            logger.info(
                "checkpoint_ttl_maintenance_skipped",
                thread_id=thread_id,
                reason="no_retained_state",
                configured_interval_seconds=max(0, settings.checkpoint_ttl_maintenance_interval_seconds),
            )
            return chat_ok

        force_refresh = self.has_active_checkpoint_state(state or {})
        refresh_claimed = True if force_refresh else await self._claim_checkpoint_ttl_refresh(thread_id)
        if not refresh_claimed:
            return chat_ok

        ttl_ok = await self.apply_session_ttl(thread_id, ttl=ttl)
        return chat_ok and ttl_ok

    async def expire_keys_with_ttl(self, keys: list[str], ttl: int) -> tuple[int, str]:
        if not keys:
            return 0, "no_keys"

        pipeline_factory = getattr(self.redis_client, "pipeline", None)
        if callable(pipeline_factory):
            pipe = pipeline_factory(transaction=False)
            for key in keys:
                pipe.expire(key, ttl)
            results = await pipe.execute()
            applied = sum(1 for result in results if result)
            return applied, "pipeline"

        applied = 0
        for key in keys:
            if await self.redis_client.expire(key, ttl):
                applied += 1
        return applied, "sequential"

    async def apply_session_ttl(self, thread_id: str, ttl: int = 86400) -> bool:
        """Apply TTL to LangGraph checkpoint keys associated with a thread."""
        try:
            patterns = [
                f"checkpoint:{thread_id}:*",
                f"checkpoint_write:{thread_id}:*",
                f"write_keys_zset:{thread_id}:*",
                f"checkpoint_latest:{thread_id}:*",
            ]

            total_start = time.perf_counter()
            total_matched = 0
            total_applied = 0
            for pattern in patterns:
                scan_start = time.perf_counter()
                keys: list[str] = []
                async for key in self.redis_client.scan_iter(match=pattern):
                    keys.append(key)
                scan_duration = (time.perf_counter() - scan_start) * 1000

                expire_start = time.perf_counter()
                applied_count, expire_mode = await self.expire_keys_with_ttl(keys, ttl)
                expire_duration = (time.perf_counter() - expire_start) * 1000

                total_matched += len(keys)
                total_applied += applied_count
                logger.info(
                    "checkpoint_ttl_pattern_processed",
                    thread_id=thread_id,
                    pattern=pattern,
                    matched_key_count=len(keys),
                    expire_applied_count=applied_count,
                    expire_mode=expire_mode,
                    scan_duration_ms=round(scan_duration, 2),
                    expire_duration_ms=round(expire_duration, 2),
                )

            logger.info(
                "checkpoint_ttl_apply_summary",
                thread_id=thread_id,
                pattern_count=len(patterns),
                matched_key_count=total_matched,
                expire_applied_count=total_applied,
                total_duration_ms=round((time.perf_counter() - total_start) * 1000, 2),
            )

            return True
        except Exception as e:
            logger.warning("apply_session_ttl_error", thread_id=thread_id, error=str(e))
            return False

    async def apply_chat_history_ttl(self, thread_id: str, ttl: int = 86400) -> bool:
        try:
            start = time.perf_counter()
            key = f"user:{thread_id.split(':')[-1]}:chat_history"
            ok = await self.redis_client.expire(key, ttl)
            logger.info(
                "chat_history_ttl_refreshed",
                thread_id=thread_id,
                key=key,
                ttl=ttl,
                refreshed=bool(ok),
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            return True
        except Exception as e:
            logger.warning("apply_chat_history_ttl_error", thread_id=thread_id, error=str(e))
            return False

    async def _claim_checkpoint_ttl_refresh(self, thread_id: str) -> bool:
        interval_seconds = max(0, settings.checkpoint_ttl_maintenance_interval_seconds)
        if interval_seconds <= 0:
            return True

        key = f"checkpoint_ttl_refresh:{thread_id}"
        set_fn = getattr(self.redis_client, "set", None)
        if not callable(set_fn):
            return True

        try:
            claimed = await set_fn(key, "1", ex=interval_seconds, nx=True)
        except Exception as exc:
            logger.warning(
                "checkpoint_ttl_refresh_claim_failed",
                thread_id=thread_id,
                error=str(exc),
            )
            return True

        if not claimed:
            logger.info(
                "checkpoint_ttl_refresh_skipped",
                thread_id=thread_id,
                reason="recently_refreshed",
                configured_interval_seconds=interval_seconds,
            )
        return bool(claimed)

    @staticmethod
    def _state_value(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    @staticmethod
    def _remaining_until(expires_at_ts: Any, *, now: float | None = None) -> int:
        if not isinstance(expires_at_ts, int | float):
            return 0
        current_time = time.time() if now is None else now
        return max(0, math.ceil(float(expires_at_ts) - current_time))

    @classmethod
    def _pending_interrupt_ttl_seconds(cls, pending_interrupt: Any, *, now: float | None = None) -> int:
        if not pending_interrupt:
            return 0
        remaining = cls._remaining_until(cls._state_value(pending_interrupt, "expires_at_ts"), now=now)
        if remaining > 0:
            return remaining
        if cls._state_value(pending_interrupt, "expires_at_ts") is not None:
            return 1
        return max(1, int(settings.pending_transaction_ttl))

    @classmethod
    def _session_stack_ttl_seconds(cls, session_stack: Any, *, now: float | None = None) -> int:
        if not isinstance(session_stack, list):
            return 0
        return max(
            [
                0,
                *[
                    cls._remaining_until(cls._state_value(session, "ttl_expires_at"), now=now)
                    for session in session_stack
                ],
            ]
        )

    @classmethod
    def _stashed_sessions_ttl_seconds(cls, stashed_sessions: Any, *, now: float | None = None) -> int:
        if not isinstance(stashed_sessions, list) or not stashed_sessions:
            return 0
        current_time = time.time() if now is None else now
        remaining_seconds: list[int] = []
        for session in stashed_sessions:
            stashed_at_ts = cls._state_value(session, "stashed_at_ts")
            if isinstance(stashed_at_ts, int | float):
                remaining_seconds.append(
                    max(0, math.ceil((float(stashed_at_ts) + STASH_RESUME_TTL_SECONDS) - current_time))
                )
            else:
                remaining_seconds.append(max(1, int(settings.checkpoint_active_ttl_seconds)))
        return max([0, *remaining_seconds])

    @staticmethod
    def context_frame_ttl_seconds(state: dict[str, Any]) -> int:
        """Return remaining TTL for fresh structured result frames in an idle thread."""
        frames = state.get("context_frames") or []
        if not isinstance(frames, list):
            return 0

        now = int(time.time())
        remaining_seconds = 0
        for frame in frames:
            created_at = getattr(frame, "created_at_ts", None)
            ttl_seconds = getattr(frame, "ttl_seconds", None)
            if isinstance(frame, dict):
                created_at = frame.get("created_at_ts")
                ttl_seconds = frame.get("ttl_seconds")
            if not isinstance(created_at, int) or not isinstance(ttl_seconds, int):
                continue
            remaining_seconds = max(remaining_seconds, (created_at + ttl_seconds) - now)
        return max(0, remaining_seconds)

    @staticmethod
    def capability_boundary_ttl_seconds(state: dict[str, Any]) -> int:
        """Return remaining TTL for unsupported-capability follow-up context."""
        boundary = state.get("capability_boundary")
        if not boundary:
            return 0

        last_updated = getattr(boundary, "last_updated_ts", None)
        ttl_seconds = getattr(boundary, "ttl_seconds", None)
        if isinstance(boundary, dict):
            last_updated = boundary.get("last_updated_ts")
            ttl_seconds = boundary.get("ttl_seconds")
        if not isinstance(last_updated, int | float) or not isinstance(ttl_seconds, int):
            return 0

        return max(0, int((float(last_updated) + ttl_seconds) - time.time()))

    @staticmethod
    def has_active_checkpoint_state(state: dict[str, Any]) -> bool:
        return bool(
            state.get("tasks")
            or state.get("waves")
            or state.get("pending_interrupt")
            or state.get("stashed_sessions")
            or state.get("session_stack")
        )

    @classmethod
    def checkpoint_ttl_seconds(cls, state: dict[str, Any]) -> int:
        """Return the checkpoint TTL needed to retain the current graph state."""
        pending_interrupt = state.get("pending_interrupt")
        pending_ttl = cls._pending_interrupt_ttl_seconds(pending_interrupt)
        if pending_ttl > 0:
            return pending_ttl

        tasks = state.get("tasks", {})
        waves = state.get("waves", [])
        stashed_sessions = state.get("stashed_sessions", [])
        session_stack = state.get("session_stack", [])
        active_default_ttl = max(1, int(settings.checkpoint_active_ttl_seconds))

        active_ttls = [
            cls._session_stack_ttl_seconds(session_stack),
            cls._stashed_sessions_ttl_seconds(stashed_sessions),
            cls.context_frame_ttl_seconds(state),
            referent_memory_ttl_seconds(state),
            cls.capability_boundary_ttl_seconds(state),
        ]
        if tasks or waves or stashed_sessions or session_stack:
            active_ttls.append(active_default_ttl)

        return max([0, *active_ttls])

    async def cleanup_if_idle(self, thread_id: str, state: dict[str, Any]) -> bool:
        """Explicitly delete thread if no active tasks, waves, or interruptions remain."""
        tasks = state.get("tasks", {})
        waves = state.get("waves", [])
        pending_interrupt = state.get("pending_interrupt")
        stashed_sessions = state.get("stashed_sessions", [])
        session_stack = state.get("session_stack", [])
        capability_boundary = state.get("capability_boundary")

        logger.info(
            "cleanup_check",
            thread_id=thread_id,
            has_tasks=bool(tasks),
            has_waves=bool(waves),
            has_interrupt=bool(pending_interrupt),
            has_stashed=bool(stashed_sessions),
            has_session_stack=bool(session_stack),
            has_capability_boundary=bool(capability_boundary),
            task_count=len(tasks) if tasks else 0,
            wave_count=len(waves) if waves else 0,
        )

        if not tasks and not waves and not pending_interrupt and not stashed_sessions and not session_stack:
            context_frame_ttl = max(
                self.context_frame_ttl_seconds(state),
                referent_memory_ttl_seconds(state),
                self.capability_boundary_ttl_seconds(state),
            )
            if context_frame_ttl > 0:
                refresh_claimed = await self._claim_checkpoint_ttl_refresh(thread_id)
                ttl_ok = True
                if refresh_claimed:
                    ttl_ok = await self.apply_session_ttl(thread_id, ttl=context_frame_ttl)
                logger.info(
                    "orchestrator_thread_retained_for_context_followup",
                    thread_id=thread_id,
                    context_frame_ttl_seconds=context_frame_ttl,
                    ttl_applied=ttl_ok,
                    ttl_refresh_claimed=refresh_claimed,
                )
                return ttl_ok
            try:
                await self.checkpointer.adelete_thread(thread_id)
                logger.info("orchestrator_thread_cleaned", thread_id=thread_id)
                return True
            except Exception as e:
                logger.warning("cleanup_thread_error", thread_id=thread_id, error=str(e))
                return False
        return True
