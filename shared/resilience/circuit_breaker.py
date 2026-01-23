"""Circuit breaker pattern for external service resilience.

Prevents cascading failures when external services are down by:
- Tracking consecutive failures
- Opening the circuit after threshold is reached
- Rejecting requests immediately while circuit is open
- Periodically allowing test requests to check if service recovered
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from shared.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Rejecting all requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3  # Failures before opening
    reset_timeout_seconds: int = 60  # Time before attempting recovery
    half_open_max_calls: int = 1  # Test calls in half-open state


@dataclass
class CircuitBreakerState:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0
    half_open_calls: int = 0


class CircuitBreaker:
    """
    Circuit breaker for protecting against external service failures.

    Usage:
        breaker = CircuitBreaker("flutterwave")

        async def call_api():
            return await breaker.call(flutterwave_client.resolve_account, args)
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self._state.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        return self._state.state == CircuitState.CLOSED

    async def call(
        self,
        func: Callable[..., Any],
        *args: Any,
        fallback: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Async function to call
            *args: Function arguments
            fallback: Value to return if circuit is open
            **kwargs: Function keyword arguments

        Returns:
            Function result or fallback if circuit is open
        """
        async with self._lock:
            self._check_state_transition()

            if self._state.state == CircuitState.OPEN:
                logger.warning(
                    "circuit_breaker_open",
                    name=self.name,
                    retry_in=self._get_retry_seconds(),
                )
                if fallback is not None:
                    return fallback
                raise CircuitOpenError(
                    f"Circuit breaker '{self.name}' is open. "
                    f"Retry in {self._get_retry_seconds()}s"
                )

            if self._state.state == CircuitState.HALF_OPEN:
                if self._state.half_open_calls >= self.config.half_open_max_calls:
                    if fallback is not None:
                        return fallback
                    raise CircuitOpenError(f"Circuit '{self.name}' half-open limit reached")
                self._state.half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result

        except Exception as e:
            await self._on_failure(e)
            raise

    def _check_state_transition(self) -> None:
        """Check if we should transition states based on time."""
        if self._state.state == CircuitState.OPEN:
            elapsed = time.time() - self._state.last_failure_time
            if elapsed >= self.config.reset_timeout_seconds:
                logger.info(
                    "circuit_breaker_half_open",
                    name=self.name,
                    elapsed=elapsed,
                )
                self._state.state = CircuitState.HALF_OPEN
                self._state.half_open_calls = 0

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            if self._state.state == CircuitState.HALF_OPEN:
                logger.info("circuit_breaker_closed", name=self.name)
                self._state.state = CircuitState.CLOSED

            self._state.failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        """Handle failed call."""
        async with self._lock:
            self._state.failure_count += 1
            self._state.last_failure_time = time.time()

            logger.warning(
                "circuit_breaker_failure",
                name=self.name,
                failure_count=self._state.failure_count,
                threshold=self.config.failure_threshold,
                error=str(error),
            )

            if self._state.failure_count >= self.config.failure_threshold:
                if self._state.state != CircuitState.OPEN:
                    logger.error(
                        "circuit_breaker_opened",
                        name=self.name,
                        failure_count=self._state.failure_count,
                    )
                self._state.state = CircuitState.OPEN

    def _get_retry_seconds(self) -> int:
        """Get seconds until circuit may close."""
        elapsed = time.time() - self._state.last_failure_time
        remaining = self.config.reset_timeout_seconds - elapsed
        return max(1, int(remaining))

    def reset(self) -> None:
        """Manually reset circuit to closed state."""
        self._state = CircuitBreakerState()
        logger.info("circuit_breaker_reset", name=self.name)


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""

    pass


# Global circuit breakers for external services
flutterwave_circuit = CircuitBreaker(
    "flutterwave",
    CircuitBreakerConfig(
        failure_threshold=3,
        reset_timeout_seconds=60,
        half_open_max_calls=1,
    ),
)
