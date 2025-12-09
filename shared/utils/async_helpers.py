"""Async utilities for background task management with error handling."""

import asyncio
from typing import Coroutine, Any, Optional
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def create_background_task(
    coro: Coroutine[Any, Any, Any],
    task_name: str = "background_task",
    log_errors: bool = True,
) -> asyncio.Task:
    """
    Create a background task with automatic error handling and logging.
    
    This wrapper ensures that exceptions in background tasks are logged
    instead of silently failing. Use this instead of asyncio.create_task()
    for fire-and-forget operations like sending notifications.
    
    Args:
        coro: Coroutine to run in background
        task_name: Name for logging purposes (use snake_case)
        log_errors: Whether to log errors (default: True)
        
    Returns:
        Created asyncio.Task
        
    Example:
        ```python
        # Instead of:
        asyncio.create_task(send_notification(phone, message))
        
        # Use:
        create_background_task(
            send_notification(phone, message),
            task_name="send_notification"
        )
        ```
    """
    task = asyncio.create_task(coro)
    
    def handle_result(task: asyncio.Task) -> None:
        """Handle task completion, logging any errors."""
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug(f"{task_name}_cancelled")
        except Exception as e:
            if log_errors:
                logger.error(
                    f"{task_name}_error",
                    error=str(e),
                    exc_info=True
                )
    
    task.add_done_callback(handle_result)
    return task
