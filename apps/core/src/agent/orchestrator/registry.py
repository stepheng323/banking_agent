"""Registry for task executors to decouple execution logic from handlers."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskExecutorProtocol(Protocol):
    """Protocol that all task executors must implement."""

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run simple execution flow."""
        ...


class ExecutorRegistry:
    """Registry for looking up task executors by name."""

    def __init__(self):
        self._executors: dict[str, TaskExecutorProtocol] = {}

    def register(self, name: str, executor: TaskExecutorProtocol) -> None:
        """Register an executor."""
        self._executors[name] = executor

    def get(self, name: str) -> TaskExecutorProtocol | None:
        """Get an executor by name."""
        return self._executors.get(name)
