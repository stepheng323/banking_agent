"""DAG-based workflow executor with dependency resolution and parallel execution."""

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from shared.types.agent_types import TaskStatus
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from .handler_registry import WorkflowHandlerRegistry
from .models import ErrorKind, TaskResult, WorkflowResult, should_continue_batch
from .workflow_context import WorkflowContext

logger = get_logger(__name__)


class WorkflowDAGExecutor:
    """
    Execute tasks respecting dependencies using DAG topology.

    Features:
    - Topological sort for execution order
    - Parallel execution of independent tasks
    - Condition evaluation
    - Error propagation (blocking dependent tasks)
    - Retry support for transient errors
    """

    def __init__(
        self,
        handler_registry: WorkflowHandlerRegistry,
        max_retries: int = 2,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.handler_registry = handler_registry
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

    async def execute(
        self,
        tasks: list[PlannedTask],
        context: WorkflowContext,
        condition_evaluator: "ConditionEvaluator | None" = None,
        validate_limits: bool = True,
    ) -> WorkflowResult:
        """
        Execute all tasks respecting dependencies.

        Args:
            tasks: List of planned tasks
            context: Execution context with services and state
            condition_evaluator: Optional evaluator for condition expressions
            validate_limits: Whether to validate aggregate limits before execution

        Returns:
            WorkflowResult with all task outcomes
        """
        if not tasks:
            return WorkflowResult(workflow_id=context.workflow_id)

        # Pre-execution: validate aggregate limits
        if validate_limits:
            limit_error = self._validate_aggregate_limits(tasks)
            if limit_error:
                return WorkflowResult(
                    workflow_id=context.workflow_id,
                    failed=[
                        TaskResult(
                            task_id=t.task_id,
                            run_id=str(uuid.uuid4()),
                            status=TaskStatus.FAILED,
                            error_message=limit_error,
                            error_kind=ErrorKind.BUSINESS,
                        )
                        for t in tasks
                    ],
                )

        # Build dependency graph
        graph = self._build_graph(tasks)
        task_map = {task.task_id: task for task in tasks}

        # Compute topological order
        try:
            order = self._topological_sort(graph, task_map)
        except ValueError as e:
            logger.error(f"[WORKFLOW] Cycle detected in task dependencies: {e}")
            # Return all tasks as failed
            return WorkflowResult(
                workflow_id=context.workflow_id,
                failed=[
                    TaskResult(
                        task_id=t.task_id,
                        run_id=str(uuid.uuid4()),
                        status=TaskStatus.FAILED,
                        error_message="Cycle in task dependencies",
                        error_kind=ErrorKind.BUSINESS,
                    )
                    for t in tasks
                ],
            )

        # Track task states
        completed: list[TaskResult] = []
        failed: list[TaskResult] = []
        skipped: list[TaskResult] = []
        blocked: list[TaskResult] = []
        failed_task_ids: set[str] = set()

        # Execute in waves (parallel groups of ready tasks)
        for wave in self._get_parallel_waves(order, graph, task_map):
            wave_coros = []
            wave_tasks: list[PlannedTask] = []

            for task_id in wave:
                task = task_map[task_id]

                # Check if blocked by failed dependency
                if self._is_blocked(task, failed_task_ids):
                    result = TaskResult(
                        task_id=task_id,
                        run_id=str(uuid.uuid4()),
                        status=TaskStatus.BLOCKED,
                        error_message="Blocked by failed dependency",
                        error_kind=ErrorKind.BUSINESS,
                    )
                    blocked.append(result)
                    context.set_result(result)
                    failed_task_ids.add(task_id)
                    continue

                # Evaluate condition
                if condition_evaluator and task.condition:
                    should_run = condition_evaluator.evaluate(task.condition, context)
                    if not should_run:
                        result = TaskResult(
                            task_id=task_id,
                            run_id=str(uuid.uuid4()),
                            status=TaskStatus.SKIPPED,
                            error_message=f"Condition '{task.condition}' evaluated to false",
                        )
                        skipped.append(result)
                        context.set_result(result)
                        continue

                # Queue for parallel execution
                wave_coros.append(self._execute_task_with_retry(task, context))
                wave_tasks.append(task)

            # Separate debit tasks (sequential) from non-debit (parallel)
            debit_executors = {"transfer", "airtime", "data"}
            debit_tasks = [t for t in wave_tasks if t.executor in debit_executors]
            non_debit_tasks = [t for t in wave_tasks if t.executor not in debit_executors]
            stopped_early = False

            # Execute debit tasks SEQUENTIALLY with continue/stop policy
            for task in debit_tasks:
                result = await self._execute_task_with_retry(task, context)

                if isinstance(result, Exception):
                    task_result = TaskResult(
                        task_id=task.task_id,
                        run_id=str(uuid.uuid4()),
                        status=TaskStatus.FAILED,
                        error_message=str(result),
                        error_kind=ErrorKind.UNKNOWN,
                    )
                    failed.append(task_result)
                    failed_task_ids.add(task.task_id)
                    context.set_result(task_result)
                    # Unknown errors → stop batch
                    stopped_early = True
                    break
                elif result.status == TaskStatus.COMPLETED:
                    completed.append(result)
                    context.set_result(result)
                else:
                    failed.append(result)
                    failed_task_ids.add(task.task_id)
                    context.set_result(result)

                    # Check if should continue or stop batch
                    if not should_continue_batch(result.provider_error):
                        logger.info(
                            f"[WORKFLOW] Stopping batch: {result.error_message} "
                            f"(provider_error: {result.provider_error})"
                        )
                        stopped_early = True
                        break
                    else:
                        logger.info(
                            f"[WORKFLOW] Continuing batch after error: {result.error_message} "
                            f"(provider_error: {result.provider_error})"
                        )

            # If stopped early, block remaining debit tasks in wave
            if stopped_early:
                remaining_idx = debit_tasks.index(task) + 1 if task in debit_tasks else len(debit_tasks)
                for remaining_task in debit_tasks[remaining_idx:]:
                    result = TaskResult(
                        task_id=remaining_task.task_id,
                        run_id=str(uuid.uuid4()),
                        status=TaskStatus.BLOCKED,
                        error_message="Batch stopped due to critical error",
                        error_kind=ErrorKind.BUSINESS,
                    )
                    blocked.append(result)
                    context.set_result(result)
                    failed_task_ids.add(remaining_task.task_id)

            # Execute non-debit tasks in PARALLEL (if not stopped)
            if non_debit_tasks and not stopped_early:
                non_debit_coros = [self._execute_task_with_retry(t, context) for t in non_debit_tasks]
                results = await asyncio.gather(*non_debit_coros, return_exceptions=True)

                for task, result in zip(non_debit_tasks, results, strict=True):
                    if isinstance(result, Exception):
                        task_result = TaskResult(
                            task_id=task.task_id,
                            run_id=str(uuid.uuid4()),
                            status=TaskStatus.FAILED,
                            error_message=str(result),
                            error_kind=ErrorKind.UNKNOWN,
                        )
                        failed.append(task_result)
                        failed_task_ids.add(task.task_id)
                        context.set_result(task_result)
                    elif result.status == TaskStatus.COMPLETED:
                        completed.append(result)
                        context.set_result(result)
                    else:
                        failed.append(result)
                        failed_task_ids.add(task.task_id)
                        context.set_result(result)
            elif non_debit_tasks and stopped_early:
                # Block non-debit tasks if stopped
                for task in non_debit_tasks:
                    result = TaskResult(
                        task_id=task.task_id,
                        run_id=str(uuid.uuid4()),
                        status=TaskStatus.BLOCKED,
                        error_message="Batch stopped due to critical error",
                        error_kind=ErrorKind.BUSINESS,
                    )
                    blocked.append(result)
                    context.set_result(result)
                    failed_task_ids.add(task.task_id)

            # If stopped, don't continue to next wave
            if stopped_early:
                break

        # If batch stopped early, ensure all unprocessed tasks are marked as blocked
        processed_ids = {r.task_id for r in completed + failed + skipped + blocked}
        for task in tasks:
            if task.task_id not in processed_ids:
                result = TaskResult(
                    task_id=task.task_id,
                    run_id=str(uuid.uuid4()),
                    status=TaskStatus.BLOCKED,
                    error_message="Batch stopped due to critical error",
                    error_kind=ErrorKind.BUSINESS,
                )
                blocked.append(result)

        return WorkflowResult(
            workflow_id=context.workflow_id,
            completed=completed,
            failed=failed,
            skipped=skipped,
            blocked=blocked,
            stopped_early=stopped_early if "stopped_early" in dir() else False,
        )

    async def _execute_task_with_retry(
        self,
        task: PlannedTask,
        context: WorkflowContext,
    ) -> TaskResult:
        """Execute a task with retry logic for transient errors."""
        run_id = str(uuid.uuid4())
        last_result: TaskResult | None = None

        for attempt in range(self.max_retries + 1):
            try:
                handler = self.handler_registry.get(task.executor)
                if not handler:
                    return TaskResult(
                        task_id=task.task_id,
                        run_id=run_id,
                        status=TaskStatus.FAILED,
                        error_message=f"No handler for executor: {task.executor}",
                        error_kind=ErrorKind.BUSINESS,
                    )

                result = await handler.execute(task, context)
                result.run_id = run_id  # Ensure consistent run_id

                if result.status == TaskStatus.COMPLETED:
                    return result

                # Check if retryable
                if result.error_kind == ErrorKind.TRANSIENT and attempt < self.max_retries:
                    logger.warning(
                        f"[WORKFLOW] Transient error on task {task.task_id}, "
                        f"attempt {attempt + 1}/{self.max_retries + 1}: {result.error_message}"
                    )
                    await asyncio.sleep(self.retry_delay_seconds * (attempt + 1))
                    last_result = result
                    continue

                return result

            except Exception as e:
                logger.error(f"[WORKFLOW] Task {task.task_id} exception: {e}", exc_info=True)
                last_result = TaskResult(
                    task_id=task.task_id,
                    run_id=run_id,
                    status=TaskStatus.FAILED,
                    error_message=str(e),
                    error_kind=ErrorKind.UNKNOWN,
                )

                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay_seconds * (attempt + 1))
                    continue

                return last_result

        return last_result or TaskResult(
            task_id=task.task_id,
            run_id=run_id,
            status=TaskStatus.FAILED,
            error_message="Max retries exceeded",
            error_kind=ErrorKind.TRANSIENT,
        )

    def _build_graph(self, tasks: list[PlannedTask]) -> dict[str, list[str]]:
        """Build adjacency list for dependency graph."""
        graph: dict[str, list[str]] = defaultdict(list)
        task_ids = {t.task_id for t in tasks}

        for task in tasks:
            # Ensure node exists even with no dependencies
            if task.task_id not in graph:
                graph[task.task_id] = []

            for dep_id in task.depends_on:
                if dep_id in task_ids:
                    # dep_id must complete before task.task_id
                    graph[dep_id].append(task.task_id)

        return dict(graph)

    def _topological_sort(
        self,
        graph: dict[str, list[str]],
        task_map: dict[str, PlannedTask],
    ) -> list[str]:
        """
        Kahn's algorithm for topological sort.

        Returns task IDs in execution order.
        Raises ValueError if cycle detected.
        """
        # Calculate in-degrees
        in_degree: dict[str, int] = dict.fromkeys(task_map, 0)
        for deps in graph.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Start with nodes that have no dependencies
        queue = [node for node, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            for neighbor in graph.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(task_map):
            raise ValueError("Cycle detected in task dependencies")

        return result

    def _get_parallel_waves(
        self,
        order: list[str],
        graph: dict[str, list[str]],
        task_map: dict[str, PlannedTask],
    ) -> list[list[str]]:
        """
        Group tasks into waves that can execute in parallel.

        Tasks in the same wave have all dependencies in previous waves.
        """
        # Compute levels (distance from root nodes)
        levels: dict[str, int] = {}
        in_degree: dict[str, int] = dict.fromkeys(task_map, 0)

        for deps in graph.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # BFS to assign levels
        queue = [(node, 0) for node, degree in in_degree.items() if degree == 0]
        while queue:
            node, level = queue.pop(0)
            levels[node] = max(levels.get(node, 0), level)

            for neighbor in graph.get(node, []):
                queue.append((neighbor, level + 1))

        # Group by level
        max_level = max(levels.values()) if levels else 0
        waves: list[list[str]] = [[] for _ in range(max_level + 1)]

        for task_id in order:
            level = levels.get(task_id, 0)
            waves[level].append(task_id)

        # Filter empty waves
        return [w for w in waves if w]

    def _is_blocked(self, task: PlannedTask, failed_task_ids: set[str]) -> bool:
        """Check if task is blocked by a failed dependency."""
        return any(dep_id in failed_task_ids for dep_id in task.depends_on)

    def _validate_aggregate_limits(self, tasks: list[PlannedTask]) -> str | None:
        """
        Validate aggregate amounts don't exceed limits.

        Returns error message if limits exceeded, None otherwise.
        """
        # Daily limits (can be made configurable or fetched from settings)
        daily_limit = 5_000_000  # ₦5M aggregate daily limit
        transfer_daily_limit = 3_000_000
        airtime_daily_limit = 500_000

        total_transfer = 0.0
        total_airtime = 0.0
        total_data = 0.0

        for task in tasks:
            amount = task.parameters.get("amount") if task.parameters else None
            if amount is None:
                continue

            try:
                amount_float = float(amount)
            except (ValueError, TypeError):
                continue

            if task.executor == "transfer":
                total_transfer += amount_float
            elif task.executor == "airtime":
                total_airtime += amount_float
            elif task.executor == "data":
                total_data += amount_float

        total_all = total_transfer + total_airtime + total_data

        if total_all > daily_limit:
            return (
                f"Aggregate amount ₦{total_all:,.0f} exceeds daily limit of ₦{daily_limit:,.0f}. "
                f"Breakdown: Transfer ₦{total_transfer:,.0f}, Airtime ₦{total_airtime:,.0f}, Data ₦{total_data:,.0f}"
            )

        if total_transfer > transfer_daily_limit:
            return f"Transfer total ₦{total_transfer:,.0f} exceeds daily limit of ₦{transfer_daily_limit:,.0f}"

        if total_airtime > airtime_daily_limit:
            return f"Airtime total ₦{total_airtime:,.0f} exceeds daily limit of ₦{airtime_daily_limit:,.0f}"

        return None


class ConditionEvaluator:
    """
    Simple expression evaluator for task conditions.

    Supports basic comparisons using task results from context.
    """

    def evaluate(self, condition: str, context: WorkflowContext) -> bool:
        """
        Evaluate a condition expression.

        Args:
            condition: Condition string (e.g., "balance > 5000")
            context: Workflow context with task results

        Returns:
            True if condition is met, False otherwise
        """
        if not condition:
            return True

        try:
            # Build namespace from task results
            namespace: dict[str, Any] = {}

            for task_id, result in context.task_results.items():
                # Make result data available by task_id
                namespace[task_id] = result.data

                # Also flatten common fields
                if "balance" in result.data:
                    namespace["balance"] = result.data["balance"]
                if "amount" in result.data:
                    namespace[f"{task_id}_amount"] = result.data["amount"]

            # Simple safe eval for basic comparisons
            # Only allow comparison operators and basic math
            allowed_operators = {"<", ">", "<=", ">=", "==", "!=", "and", "or", "not"}
            tokens = condition.split()

            # Very basic validation - in production use a proper expression parser
            for token in tokens:
                if token.isalpha() and token not in allowed_operators and token not in namespace:
                    # Unknown identifier - treat as false
                    logger.warning(f"[CONDITION] Unknown identifier in condition: {token}")
                    return False

            # Use eval with restricted namespace (no builtins)
            result = eval(condition, {"__builtins__": {}}, namespace)
            return bool(result)

        except Exception as e:
            logger.warning(f"[CONDITION] Failed to evaluate '{condition}': {e}")
            return False
