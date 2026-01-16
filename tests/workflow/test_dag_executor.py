"""Unit tests for DAG executor - graph building, topological sort, parallel waves."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.core.src.agent.shared.batch.workflow.dag_executor import (
    ConditionEvaluator,
    WorkflowDAGExecutor,
)
from apps.core.src.agent.shared.batch.workflow.handler_registry import WorkflowHandlerRegistry
from apps.core.src.agent.shared.batch.workflow.models import ErrorKind, TaskResult
from apps.core.src.agent.shared.batch.workflow.workflow_context import WorkflowContext
from shared.types.agent_types import TaskStatus
from shared.types.planner import PlannedTask


def make_task(
    task_id: str,
    executor: str = "transfer",
    depends_on: list[str] | None = None,
    condition: str | None = None,
    parameters: dict | None = None,
) -> PlannedTask:
    """Helper to create PlannedTask for testing."""
    return PlannedTask(
        task_id=task_id,
        action=f"test_{executor}",
        executor=executor,
        instruction=f"Test instruction for {task_id}",
        depends_on=depends_on or [],
        condition=condition,
        parameters=parameters or {},
    )


def make_context(workflow_id: str = "test-workflow") -> WorkflowContext:
    """Helper to create WorkflowContext for testing."""
    return WorkflowContext(
        phone_number="2348012345678",
        user_id="test-user-id",
        workflow_id=workflow_id,
        pin_verified=True,
    )


def make_success_handler() -> MagicMock:
    """Create a mock handler that returns success."""
    handler = MagicMock()
    handler.execute = AsyncMock(
        side_effect=lambda task, ctx: TaskResult(
            task_id=task.task_id,
            run_id=str(uuid.uuid4()),
            status=TaskStatus.COMPLETED,
            data={"success": True},
        )
    )
    return handler


def make_failure_handler(error_kind: ErrorKind = ErrorKind.BUSINESS) -> MagicMock:
    """Create a mock handler that returns failure."""
    handler = MagicMock()
    handler.execute = AsyncMock(
        side_effect=lambda task, ctx: TaskResult(
            task_id=task.task_id,
            run_id=str(uuid.uuid4()),
            status=TaskStatus.FAILED,
            error_message="Test failure",
            error_kind=error_kind,
        )
    )
    return handler


class TestGraphBuilding:
    """Tests for dependency graph construction."""

    def test_build_graph_no_dependencies(self):
        """Tasks with no dependencies should have empty adjacency lists."""
        tasks = [
            make_task("task1"),
            make_task("task2"),
            make_task("task3"),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)

        assert "task1" in graph
        assert "task2" in graph
        assert "task3" in graph
        # Each task has no dependents
        assert graph["task1"] == []
        assert graph["task2"] == []
        assert graph["task3"] == []

    def test_build_graph_linear_dependencies(self):
        """task1 -> task2 -> task3 (linear chain)."""
        tasks = [
            make_task("task1"),
            make_task("task2", depends_on=["task1"]),
            make_task("task3", depends_on=["task2"]),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)

        # task1 has task2 as dependent
        assert "task2" in graph["task1"]
        # task2 has task3 as dependent
        assert "task3" in graph["task2"]
        # task3 has no dependents
        assert graph["task3"] == []

    def test_build_graph_diamond_dependencies(self):
        """Diamond pattern: task1 -> (task2, task3) -> task4."""
        tasks = [
            make_task("task1"),
            make_task("task2", depends_on=["task1"]),
            make_task("task3", depends_on=["task1"]),
            make_task("task4", depends_on=["task2", "task3"]),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)

        # task1 feeds into task2 and task3
        assert set(graph["task1"]) == {"task2", "task3"}
        # task2 and task3 feed into task4
        assert graph["task2"] == ["task4"]
        assert graph["task3"] == ["task4"]


class TestTopologicalSort:
    """Tests for topological sorting."""

    def test_topological_sort_independent_tasks(self):
        """Independent tasks can appear in any order."""
        tasks = [make_task("a"), make_task("b"), make_task("c")]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)
        task_map = {t.task_id: t for t in tasks}
        order = executor._topological_sort(graph, task_map)

        assert set(order) == {"a", "b", "c"}
        assert len(order) == 3

    def test_topological_sort_linear_chain(self):
        """Linear chain must preserve order."""
        tasks = [
            make_task("first"),
            make_task("second", depends_on=["first"]),
            make_task("third", depends_on=["second"]),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)
        task_map = {t.task_id: t for t in tasks}
        order = executor._topological_sort(graph, task_map)

        # first must come before second, second before third
        assert order.index("first") < order.index("second")
        assert order.index("second") < order.index("third")

    def test_topological_sort_diamond(self):
        """Diamond pattern respects all dependencies."""
        tasks = [
            make_task("root"),
            make_task("left", depends_on=["root"]),
            make_task("right", depends_on=["root"]),
            make_task("merge", depends_on=["left", "right"]),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)
        task_map = {t.task_id: t for t in tasks}
        order = executor._topological_sort(graph, task_map)

        # root must come first
        assert order[0] == "root"
        # merge must come last
        assert order[-1] == "merge"
        # left and right must come before merge
        assert order.index("left") < order.index("merge")
        assert order.index("right") < order.index("merge")


class TestParallelWaves:
    """Tests for parallel wave generation."""

    def test_parallel_waves_independent(self):
        """Independent tasks should be in a single wave."""
        tasks = [make_task("a"), make_task("b"), make_task("c")]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)
        task_map = {t.task_id: t for t in tasks}
        order = executor._topological_sort(graph, task_map)
        waves = executor._get_parallel_waves(order, graph, task_map)

        # All tasks in one wave
        assert len(waves) == 1
        assert set(waves[0]) == {"a", "b", "c"}

    def test_parallel_waves_linear(self):
        """Linear chain should have one task per wave."""
        tasks = [
            make_task("first"),
            make_task("second", depends_on=["first"]),
            make_task("third", depends_on=["second"]),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)
        task_map = {t.task_id: t for t in tasks}
        order = executor._topological_sort(graph, task_map)
        waves = executor._get_parallel_waves(order, graph, task_map)

        # Three waves, one task each
        assert len(waves) == 3
        assert waves[0] == ["first"]
        assert waves[1] == ["second"]
        assert waves[2] == ["third"]

    def test_parallel_waves_diamond(self):
        """Diamond pattern: root alone, left+right together, merge alone."""
        tasks = [
            make_task("root"),
            make_task("left", depends_on=["root"]),
            make_task("right", depends_on=["root"]),
            make_task("merge", depends_on=["left", "right"]),
        ]
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        graph = executor._build_graph(tasks)
        task_map = {t.task_id: t for t in tasks}
        order = executor._topological_sort(graph, task_map)
        waves = executor._get_parallel_waves(order, graph, task_map)

        # Three waves
        assert len(waves) == 3
        assert waves[0] == ["root"]
        assert set(waves[1]) == {"left", "right"}
        assert waves[2] == ["merge"]


class TestBlockedTasks:
    """Tests for blocked task detection."""

    def test_is_blocked_no_failed_deps(self):
        """Task with no failed dependencies is not blocked."""
        task = make_task("task", depends_on=["dep1", "dep2"])
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        failed_ids = set()
        assert executor._is_blocked(task, failed_ids) is False

    def test_is_blocked_with_failed_dep(self):
        """Task with a failed dependency is blocked."""
        task = make_task("task", depends_on=["dep1", "dep2"])
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        failed_ids = {"dep1"}
        assert executor._is_blocked(task, failed_ids) is True

    def test_is_blocked_unrelated_failure(self):
        """Task is not blocked by unrelated task failure."""
        task = make_task("task", depends_on=["dep1", "dep2"])
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)

        failed_ids = {"other_task"}
        assert executor._is_blocked(task, failed_ids) is False


class TestConditionEvaluator:
    """Tests for condition evaluation."""

    def test_evaluate_empty_condition(self):
        """Empty condition returns True."""
        evaluator = ConditionEvaluator()
        context = make_context()

        assert evaluator.evaluate(None, context) is True
        assert evaluator.evaluate("", context) is True

    def test_evaluate_simple_comparison(self):
        """Simple numeric comparison works."""
        evaluator = ConditionEvaluator()
        context = make_context()

        # Add balance to context
        context.task_results["balance_check"] = TaskResult(
            task_id="balance_check",
            run_id="test",
            status=TaskStatus.COMPLETED,
            data={"balance": 10000},
        )

        assert evaluator.evaluate("balance > 5000", context) is True
        assert evaluator.evaluate("balance < 5000", context) is False

    def test_evaluate_unknown_variable(self):
        """Unknown variable returns False."""
        evaluator = ConditionEvaluator()
        context = make_context()

        assert evaluator.evaluate("unknown_var > 100", context) is False


@pytest.mark.asyncio
class TestWorkflowExecution:
    """Integration tests for workflow execution."""

    async def test_execute_empty_tasks(self):
        """Empty task list returns empty result."""
        registry = WorkflowHandlerRegistry()
        executor = WorkflowDAGExecutor(registry)
        context = make_context()

        result = await executor.execute([], context)

        assert result.total == 0
        assert result.completed == []
        assert result.failed == []

    async def test_execute_single_task_success(self):
        """Single successful task."""
        registry = WorkflowHandlerRegistry()
        registry.register("transfer", make_success_handler())
        executor = WorkflowDAGExecutor(registry)
        context = make_context()
        tasks = [make_task("task1", executor="transfer")]

        result = await executor.execute(tasks, context)

        assert len(result.completed) == 1
        assert result.completed[0].task_id == "task1"
        assert len(result.failed) == 0

    async def test_execute_single_task_failure(self):
        """Single failed task."""
        registry = WorkflowHandlerRegistry()
        registry.register("transfer", make_failure_handler())
        executor = WorkflowDAGExecutor(registry)
        context = make_context()
        tasks = [make_task("task1", executor="transfer")]

        result = await executor.execute(tasks, context)

        assert len(result.completed) == 0
        assert len(result.failed) == 1
        assert result.failed[0].task_id == "task1"

    async def test_execute_dependency_blocking(self):
        """Failed task blocks dependent tasks."""
        registry = WorkflowHandlerRegistry()
        registry.register("transfer", make_failure_handler())
        executor = WorkflowDAGExecutor(registry)
        context = make_context()
        tasks = [
            make_task("task1", executor="transfer"),
            make_task("task2", executor="transfer", depends_on=["task1"]),
        ]

        result = await executor.execute(tasks, context)

        assert len(result.failed) == 1
        assert result.failed[0].task_id == "task1"
        assert len(result.blocked) == 1
        assert result.blocked[0].task_id == "task2"

    async def test_execute_parallel_tasks(self):
        """Independent tasks execute in parallel."""
        registry = WorkflowHandlerRegistry()
        registry.register("transfer", make_success_handler())
        executor = WorkflowDAGExecutor(registry)
        context = make_context()
        tasks = [
            make_task("task1", executor="transfer"),
            make_task("task2", executor="transfer"),
            make_task("task3", executor="transfer"),
        ]

        result = await executor.execute(tasks, context)

        assert len(result.completed) == 3
        assert {r.task_id for r in result.completed} == {"task1", "task2", "task3"}

    async def test_execute_condition_skip(self):
        """Task with false condition is skipped."""
        registry = WorkflowHandlerRegistry()
        registry.register("transfer", make_success_handler())
        executor = WorkflowDAGExecutor(registry)
        context = make_context()
        evaluator = ConditionEvaluator()
        tasks = [make_task("task1", executor="transfer", condition="balance > 1000000")]

        result = await executor.execute(tasks, context, condition_evaluator=evaluator)

        assert len(result.completed) == 0
        assert len(result.skipped) == 1
        assert result.skipped[0].task_id == "task1"

    async def test_execute_missing_handler(self):
        """Task with no handler fails."""
        registry = WorkflowHandlerRegistry()  # No handlers registered
        executor = WorkflowDAGExecutor(registry)
        context = make_context()
        tasks = [make_task("task1", executor="transfer")]

        result = await executor.execute(tasks, context)

        assert len(result.failed) == 1
        assert "No handler" in result.failed[0].error_message
