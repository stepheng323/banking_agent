"""Turn progress tracking for long-running chat tasks."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from shared.i18n import render_message

FIRST_PROGRESS_DELAY_SECONDS = 5.0
SECOND_PROGRESS_DELAY_SECONDS = 15.0
MIN_PROGRESS_STAGE_AGE_SECONDS = 1.0
PROGRESS_POLL_INTERVAL_SECONDS = 0.25
MAX_PROGRESS_MESSAGES = 2


@dataclass(frozen=True, slots=True)
class ProgressStagePolicy:
    visible_to_user: bool
    first_progress_delay_seconds: float = FIRST_PROGRESS_DELAY_SECONDS
    followup_progress_delay_seconds: float = SECOND_PROGRESS_DELAY_SECONDS
    min_stage_age_seconds: float = MIN_PROGRESS_STAGE_AGE_SECONDS


_DEFAULT_PROGRESS_STAGE_POLICY = ProgressStagePolicy(visible_to_user=False)

_PROGRESS_STAGE_POLICIES: dict[str, ProgressStagePolicy] = {
    "query.resolving_followup": ProgressStagePolicy(visible_to_user=False),
    "query.fetching_transactions": ProgressStagePolicy(visible_to_user=True),
    "query.comparing_periods": ProgressStagePolicy(visible_to_user=True),
    "transfer.resolving_recipient": ProgressStagePolicy(visible_to_user=False),
    "transfer.confirming_details": ProgressStagePolicy(visible_to_user=False),
    "transfer.authorizing_transfer": ProgressStagePolicy(visible_to_user=False),
    "transfer.processing_transfer": ProgressStagePolicy(visible_to_user=True),
}


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


def progress_stage_policy(stage_key: str | None) -> ProgressStagePolicy | None:
    """Return the configured policy for a progress stage."""
    if stage_key is None:
        return None
    return _PROGRESS_STAGE_POLICIES.get(stage_key, _DEFAULT_PROGRESS_STAGE_POLICY)


def is_progress_stage_user_visible(stage_key: str | None) -> bool:
    """Return whether a progress stage can emit visible chat progress."""
    policy = progress_stage_policy(stage_key)
    return bool(policy and policy.visible_to_user)


def next_progress_delay_seconds(stage_key: str | None, progress_count: int) -> float | None:
    """Return the absolute elapsed-time threshold for the next progress update."""
    policy = progress_stage_policy(stage_key)
    if policy is None or not policy.visible_to_user:
        return None
    thresholds = (policy.first_progress_delay_seconds, policy.followup_progress_delay_seconds)
    if progress_count < 0 or progress_count >= len(thresholds):
        return None
    return thresholds[progress_count]


def seconds_until_progress_eligible(
    snapshot: TurnProgressSnapshot,
    *,
    now: float | None = None,
) -> float | None:
    """Return seconds until the next progress message becomes useful to send."""
    policy = progress_stage_policy(snapshot.stage_key)
    if policy is None or not policy.visible_to_user:
        return None

    next_delay = next_progress_delay_seconds(snapshot.stage_key, snapshot.progress_count)
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
        if stage_age < policy.min_stage_age_seconds:
            waits.append(policy.min_stage_age_seconds - stage_age)

    return max(0.0, min(waits)) if waits else 0.0


def should_emit_progress(
    snapshot: TurnProgressSnapshot,
    *,
    now: float | None = None,
) -> bool:
    """Return whether a progress message should be emitted now."""
    wait_seconds = seconds_until_progress_eligible(snapshot, now=now)
    return wait_seconds == 0.0


def _render_progress_scope_label(
    *,
    locale: str,
    stage_metadata: dict[str, Any] | None,
) -> str | None:
    if not stage_metadata:
        return None
    counterparty = stage_metadata.get("counterparty_label")
    direction = stage_metadata.get("direction")
    category = stage_metadata.get("category_label")
    time_label = stage_metadata.get("time_label")

    if isinstance(counterparty, str) and counterparty.strip():
        counterparty_value = counterparty.strip()
        if direction == "sent":
            return render_message("progress.scope.sent_to", locale, {"counterparty": counterparty_value})
        if direction == "received":
            return render_message("progress.scope.received_from", locale, {"counterparty": counterparty_value})
        return render_message("progress.scope.transactions_with", locale, {"counterparty": counterparty_value})

    if isinstance(category, str) and category.strip():
        if direction == "sent":
            base_scope = render_message("progress.scope.sent", locale)
        elif direction == "received":
            base_scope = render_message("progress.scope.received", locale)
        elif direction == "outgoing":
            base_scope = render_message("progress.scope.outgoing_transactions", locale)
        elif direction == "incoming":
            base_scope = render_message("progress.scope.incoming_transactions", locale)
        else:
            base_scope = render_message("progress.scope.transactions", locale)
        return render_message(
            "progress.scope.with_category",
            locale,
            {"scope_label": base_scope, "category": category.strip()},
        )

    scope_label = stage_metadata.get("scope_label")
    if isinstance(scope_label, str) and scope_label.strip():
        scope_value = scope_label.strip()
        if not isinstance(time_label, str) or not time_label.strip() or time_label.strip() not in scope_value:
            return scope_value
    return None


def render_progress_message(
    *,
    stage_key: str,
    progress_count: int,
    locale: str,
    stage_metadata: dict[str, Any] | None = None,
) -> str:
    """Render a chat-safe progress message for the active stage."""
    scope_label = _render_progress_scope_label(locale=locale, stage_metadata=stage_metadata)
    variant = "first" if progress_count <= 0 else "followup"
    scoped_key_by_stage = {
        "query.resolving_followup": {
            "first": "progress.query.resolving_followup.first_scoped",
            "followup": "progress.query.resolving_followup.followup_scoped",
        },
        "query.fetching_transactions": {
            "first": "progress.query.fetching_transactions.first_scoped",
            "followup": "progress.query.fetching_transactions.followup_scoped",
        },
        "query.comparing_periods": {
            "first": "progress.query.comparing_periods.first_scoped",
            "followup": "progress.query.comparing_periods.followup_scoped",
        },
        "transfer.processing_transfer": {
            "first": "progress.transfer.processing_transfer.first_scoped",
            "followup": "progress.transfer.processing_transfer.followup_scoped",
        },
    }
    generic_key_by_stage = {
        "query.resolving_followup": {
            "first": "progress.query.resolving_followup.first_generic",
            "followup": "progress.query.resolving_followup.followup_generic",
        },
        "query.fetching_transactions": {
            "first": "progress.query.fetching_transactions.first_generic",
            "followup": "progress.query.fetching_transactions.followup_generic",
        },
        "query.comparing_periods": {
            "first": "progress.query.comparing_periods.first_generic",
            "followup": "progress.query.comparing_periods.followup_generic",
        },
        "transfer.resolving_recipient": {
            "first": "progress.transfer.resolving_recipient.first_generic",
            "followup": "progress.transfer.resolving_recipient.followup_generic",
        },
        "transfer.confirming_details": {
            "first": "progress.transfer.confirming_details.first_generic",
            "followup": "progress.transfer.confirming_details.followup_generic",
        },
        "transfer.authorizing_transfer": {
            "first": "progress.transfer.authorizing_transfer.first_generic",
            "followup": "progress.transfer.authorizing_transfer.followup_generic",
        },
        "transfer.processing_transfer": {
            "first": "progress.transfer.processing_transfer.first_generic",
            "followup": "progress.transfer.processing_transfer.followup_generic",
        },
    }

    scoped_key = scoped_key_by_stage.get(stage_key, {}).get(variant)
    if scoped_key:
        if stage_key.startswith("transfer.") and stage_metadata and "amount" in stage_metadata:
            return render_message(scoped_key, locale, stage_metadata)
        if scope_label:
            return render_message(scoped_key, locale, {"scope_label": scope_label})

    generic_key = generic_key_by_stage.get(stage_key, {}).get(variant)
    if generic_key:
        return render_message(generic_key, locale)

    fallback_key = "progress.common.first_generic" if progress_count <= 0 else "progress.common.followup_generic"
    return render_message(fallback_key, locale)
