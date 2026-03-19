"""Turn progress tracking for long-running chat tasks."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

FIRST_PROGRESS_DELAY_SECONDS = 3.0
SECOND_PROGRESS_DELAY_SECONDS = 9.0
MIN_PROGRESS_STAGE_AGE_SECONDS = 0.75
PROGRESS_POLL_INTERVAL_SECONDS = 0.25
MAX_PROGRESS_MESSAGES = 2


@dataclass(slots=True)
class TurnProgressSnapshot:
    stage_key: str | None
    started_at: float
    stage_started_at: float | None
    last_progress_sent_at: float | None
    progress_count: int
    stage_metadata: dict[str, Any] | None
    locale: str


class TurnProgressTracker:
    """Tracks the current long-task stage for one orchestrator turn."""

    def __init__(self, *, locale: str) -> None:
        self._started_at = time.monotonic()
        self._stage_key: str | None = None
        self._stage_started_at: float | None = None
        self._last_progress_sent_at: float | None = None
        self._progress_count = 0
        self._stage_metadata: dict[str, Any] | None = None
        self._locale = locale
        self._lock = asyncio.Lock()
        self._update_event = asyncio.Event()

    async def set_stage(self, stage_key: str, *, stage_metadata: dict[str, Any] | None = None) -> None:
        async with self._lock:
            if stage_key != self._stage_key:
                self._stage_started_at = time.monotonic()
            self._stage_key = stage_key
            self._stage_metadata = dict(stage_metadata) if stage_metadata else None
            self._update_event.set()

    async def snapshot(self) -> TurnProgressSnapshot:
        async with self._lock:
            metadata = dict(self._stage_metadata) if self._stage_metadata else None
            return TurnProgressSnapshot(
                stage_key=self._stage_key,
                started_at=self._started_at,
                stage_started_at=self._stage_started_at,
                last_progress_sent_at=self._last_progress_sent_at,
                progress_count=self._progress_count,
                stage_metadata=metadata,
                locale=self._locale,
            )

    async def record_progress_sent(self) -> None:
        async with self._lock:
            self._last_progress_sent_at = time.monotonic()
            self._progress_count += 1
            self._update_event.set()

    async def wait_for_update(self, timeout_seconds: float | None = None) -> None:
        try:
            if timeout_seconds is None:
                await self._update_event.wait()
            else:
                await asyncio.wait_for(self._update_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return
        finally:
            self._update_event.clear()


def next_progress_delay_seconds(progress_count: int) -> float | None:
    """Return the absolute elapsed-time threshold for the next progress update."""
    thresholds = (FIRST_PROGRESS_DELAY_SECONDS, SECOND_PROGRESS_DELAY_SECONDS)
    if progress_count < 0 or progress_count >= len(thresholds):
        return None
    return thresholds[progress_count]


def seconds_until_progress_eligible(
    snapshot: TurnProgressSnapshot,
    *,
    now: float | None = None,
) -> float | None:
    """Return seconds until the next progress message becomes useful to send."""
    next_delay = next_progress_delay_seconds(snapshot.progress_count)
    if next_delay is None:
        return None

    now_value = time.monotonic() if now is None else now
    waits: list[float] = []

    elapsed = now_value - snapshot.started_at
    if elapsed < next_delay:
        waits.append(next_delay - elapsed)

    if snapshot.stage_key is None or snapshot.stage_started_at is None:
        waits.append(PROGRESS_POLL_INTERVAL_SECONDS)
    else:
        stage_age = now_value - snapshot.stage_started_at
        if stage_age < MIN_PROGRESS_STAGE_AGE_SECONDS:
            waits.append(MIN_PROGRESS_STAGE_AGE_SECONDS - stage_age)

    return max(0.0, min(waits)) if waits else 0.0


def should_emit_progress(
    snapshot: TurnProgressSnapshot,
    *,
    now: float | None = None,
) -> bool:
    """Return whether a progress message should be emitted now."""
    wait_seconds = seconds_until_progress_eligible(snapshot, now=now)
    return wait_seconds == 0.0


def render_progress_message(
    *,
    stage_key: str,
    progress_count: int,
    locale: str,
    stage_metadata: dict[str, Any] | None = None,
) -> str:
    """Render a chat-safe progress message for the active stage."""
    del locale
    del stage_metadata

    first_messages = {
        "query.fetching_transactions": "Fetching your transactions.",
        "query.comparing_periods": "Comparing the matching time periods.",
        "transfer.resolving_recipient": "Resolving the recipient details.",
        "transfer.confirming_details": "Confirming the transfer details.",
        "transfer.authorizing_transfer": "Authorizing the transfer.",
        "transfer.processing_transfer": "Processing the transfer.",
    }
    followup_messages = {
        "query.fetching_transactions": "Still working. I am fetching your transactions.",
        "query.comparing_periods": "Still working. I am comparing the matching time periods.",
        "transfer.resolving_recipient": "Still working. I am resolving the recipient details.",
        "transfer.confirming_details": "Still working. I am confirming the transfer details.",
        "transfer.authorizing_transfer": "Still working. I am authorizing the transfer.",
        "transfer.processing_transfer": "Still working. I am processing the transfer.",
    }

    if progress_count <= 0:
        return first_messages.get(stage_key, "Working on your request.")
    return followup_messages.get(stage_key, "Still working on your request.")
