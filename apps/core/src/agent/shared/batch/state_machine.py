"""Batch state machine for managing execution lifecycle."""

from typing import Any

from shared.cache.redis_client import Redis
from shared.utils.logging import get_logger

from .utils import ExecutionState

logger = get_logger(__name__)


class BatchStateMachine:
    """
    Manages the lifecycle state of a batch execution.

    Enforces valid transitions and persists state to Redis.
    """

    # Valid transitions map: Current State -> {Allowed Next States}
    TRANSITIONS = {
        ExecutionState.IDLE: {
            ExecutionState.PLANNED,
            ExecutionState.READY_FOR_AUTH,
            ExecutionState.AUTHORIZED,  # Backward compat
            ExecutionState.EXECUTING_BATCH,  # Retries
        },
        ExecutionState.PLANNED: {ExecutionState.COLLECTING, ExecutionState.CANCELLED, ExecutionState.EXPIRED},
        ExecutionState.COLLECTING: {
            ExecutionState.READY_FOR_AUTH,
            ExecutionState.CANCELLED,
            ExecutionState.EXPIRED,
            ExecutionState.PLANNED,  # If plan changes
        },
        ExecutionState.READY_FOR_AUTH: {
            ExecutionState.AUTHORIZED,
            ExecutionState.CANCELLED,
            ExecutionState.EXPIRED,
            ExecutionState.COLLECTING,  # Backtrack
        },
        ExecutionState.AUTHORIZED: {
            ExecutionState.EXECUTING_BATCH,
            ExecutionState.CANCELLED,
            ExecutionState.EXPIRED,
        },
        ExecutionState.EXECUTING_BATCH: {
            ExecutionState.COMPLETED,
            ExecutionState.STOPPED,
            ExecutionState.CANCELLED,  # Hard cancel?
        },
        ExecutionState.COMPLETED: {ExecutionState.IDLE, ExecutionState.PLANNED},  # New batch
        ExecutionState.STOPPED: {ExecutionState.IDLE, ExecutionState.PLANNED},
        ExecutionState.CANCELLED: {ExecutionState.IDLE, ExecutionState.PLANNED},
        ExecutionState.EXPIRED: {ExecutionState.IDLE, ExecutionState.PLANNED},
    }

    def __init__(self, redis_client: Redis, phone_number: str):
        self.redis = redis_client
        self.phone_number = phone_number
        self.state_key = f"queue:{phone_number}:execution_state"

    async def get_state(self) -> str:
        """Get current state (default IDLE)."""
        state = await self.redis.get(self.state_key)
        return state or ExecutionState.IDLE

    async def transition_to(self, new_state: str, context: dict[str, Any] | None = None) -> bool:
        """
        Transition to a new state if valid.

        Args:
            new_state: Target state
            context: Optional metadata to log or store

        Returns:
            True if transition succeeded, False if invalid
        """
        current_state = await self.get_state()

        if new_state == current_state:
            return True

        allowed = self.TRANSITIONS.get(current_state, set())
        # Always allow transitioning to IDLE (cleanup) or from IDLE (start) if implicit
        if new_state == ExecutionState.IDLE:
            allowed.add(ExecutionState.IDLE)

        # Override for forced cleanup or resets
        if current_state in {
            ExecutionState.COMPLETED,
            ExecutionState.STOPPED,
            ExecutionState.CANCELLED,
            ExecutionState.EXPIRED,
        }:
            # Allow starting over
            if new_state == ExecutionState.PLANNED:
                allowed.add(ExecutionState.PLANNED)

        if new_state not in allowed:
            logger.warning(f"[BATCH-STATE] Invalid transition for {self.phone_number}: {current_state} -> {new_state}")
            return False

        # Store new state
        # TTL: short for transient states, longer for active
        ttl = 3600  # 1 hour default
        if new_state in {ExecutionState.IDLE, ExecutionState.COMPLETED, ExecutionState.CANCELLED}:
            ttl = 300  # 5 min cleanup

        await self.redis.set(self.state_key, new_state, ex=ttl)

        logger.info(
            f"[BATCH-STATE] Transitioned {self.phone_number}: {current_state} -> {new_state} "
            f"{f'({context})' if context else ''}"
        )
        return True

    async def clear(self) -> None:
        """Reset state to IDLE."""
        await self.redis.delete(self.state_key)
