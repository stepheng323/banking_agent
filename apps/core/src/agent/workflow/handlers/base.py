"""Base handler utilities."""

from apps.core.src.agent.workflow.models import TaskResult, TaskStatus


def success_result(task_id: str, data: dict | None = None) -> TaskResult:
    """Create a successful task result."""
    return TaskResult(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        data=data or {},
    )


def needs_input_result(
    task_id: str,
    missing_fields: list[str],
    prompt: str,
) -> TaskResult:
    """Create a result indicating more input is needed."""
    return TaskResult(
        task_id=task_id,
        status=TaskStatus.NEEDS_INPUT,
        missing_fields=missing_fields,
        user_prompt=prompt,
    )


def failed_result(task_id: str, error: str) -> TaskResult:
    """Create a failed task result."""
    return TaskResult(
        task_id=task_id,
        status=TaskStatus.FAILED,
        error=error,
    )
