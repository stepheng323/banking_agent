import time

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.gate.stages.stale_context_arbitration import (
    StaleContextSnapshot,
    is_strict_context_selector,
)
from banking.support.models import PendingReferenceState, SupportContext


def test_is_strict_context_selector_fresh_frame() -> None:
    now = time.time()
    frame = ContextFrame(
        frame_id="frame-123",
        frame_type=ContextFrameType.GENERIC,
        items=[],
        created_at_ts=int(now - 30),  # 30 seconds ago
        ttl_seconds=600,
    )
    snapshot = StaleContextSnapshot(
        support_user_id=None,
        support_context=None,
        has_pending_reference=False,
        has_receipt_thread=False,
        has_support_transaction_context=False,
        has_recent_batch_reference=False,
        latest_context_frame=frame,
    )

    # "1" is a valid context frame selector (matches _CONTEXT_FRAME_SELECTOR_RE)
    assert is_strict_context_selector("1", snapshot) is True


def test_is_strict_context_selector_stale_frame() -> None:
    now = time.time()
    frame = ContextFrame(
        frame_id="frame-123",
        frame_type=ContextFrameType.GENERIC,
        items=[],
        created_at_ts=int(now - 200),  # 200 seconds ago (> 180s)
        ttl_seconds=600,
    )
    snapshot = StaleContextSnapshot(
        support_user_id=None,
        support_context=None,
        has_pending_reference=False,
        has_receipt_thread=False,
        has_support_transaction_context=False,
        has_recent_batch_reference=False,
        latest_context_frame=frame,
    )

    assert is_strict_context_selector("1", snapshot) is False


def test_is_strict_context_selector_fresh_pending_reference() -> None:
    now = time.time()
    pending = PendingReferenceState(
        source="recent_batch",
        candidates=[],
        expires_at_ts=now + 850,  # Created 50 seconds ago (expires in 850s, TTL is 900s)
    )
    support_ctx = SupportContext(pending_reference=pending)
    snapshot = StaleContextSnapshot(
        support_user_id="user-123",
        support_context=support_ctx,
        has_pending_reference=True,
        has_receipt_thread=False,
        has_support_transaction_context=False,
        has_recent_batch_reference=False,
        latest_context_frame=None,
    )

    # A short pending reference selector label
    assert is_strict_context_selector("Tolu", snapshot) is True


def test_is_strict_context_selector_stale_pending_reference() -> None:
    now = time.time()
    pending = PendingReferenceState(
        source="recent_batch",
        candidates=[],
        expires_at_ts=now + 700,  # Created 200 seconds ago (expires in 700s, TTL is 900s)
    )
    support_ctx = SupportContext(pending_reference=pending)
    snapshot = StaleContextSnapshot(
        support_user_id="user-123",
        support_context=support_ctx,
        has_pending_reference=True,
        has_receipt_thread=False,
        has_support_transaction_context=False,
        has_recent_batch_reference=False,
        latest_context_frame=None,
    )

    assert is_strict_context_selector("Tolu", snapshot) is False
