"""PIN verification handling for authorization flows."""

from dataclasses import dataclass

from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PinResult:
    """Result of PIN verification check."""

    verified: bool
    exceeded_max: bool = False
    error: str | None = None
    retry_count: int = 0
    user_id: str | None = None


def handle_pin_failure(pin_result, max_retries: int = 3) -> PinResult:
    """
    Process PIN verification failure.

    Args:
        pin_result: Result from authorization service
        max_retries: Maximum allowed retry attempts

    Returns:
        PinResult with failure details
    """
    retry_count = pin_result.retry_count if pin_result else 0
    error_msg = pin_result.error if pin_result else "PIN verification failed"

    exceeded = retry_count >= max_retries

    if exceeded:
        logger.warning(
            "pin_max_retries_exceeded",
            retry_count=retry_count,
            max_retries=max_retries,
        )

    return PinResult(
        verified=False,
        exceeded_max=exceeded,
        error=error_msg,
        retry_count=retry_count,
        user_id=pin_result.user_id if pin_result else None,
    )
