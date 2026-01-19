"""DAG-based workflow executor.

Executes planned tasks in parallel waves, respecting dependencies.
Handles preflight validation, authorization gating, and partial failures.
"""

import asyncio
from collections import defaultdict
from typing import Any

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.handler_registry import (
    HandlerRegistry,
    UnsupportedExecutorResult,
)
from apps.core.src.agent.workflow.models import (
    TaskResult,
    TaskStatus,
    WorkflowResult,
    WorkflowStatus,
    requires_authorization,
)

logger = get_logger(__name__)

# Concurrency cap for parallel execution
MAX_PARALLEL_TASKS = 3


class WorkflowDAGExecutor:
    """Executes workflow tasks as a DAG with parallel waves."""
    
    def __init__(self, registry: HandlerRegistry):
        self.registry = registry
    
    async def execute(
        self,
        tasks: list[PlannedTask],
        ctx: WorkflowContext,
    ) -> WorkflowResult:
        """
        Execute workflow tasks.
        
        Flow:
        1. Build dependency graph
        2. Check for unsupported executors
        3. Preflight: validate all tasks, collect missing fields
        4. If any NEEDS_INPUT → return waiting_for_input
        5. Auth gate: if MONEY_MOVE and not pin_verified → return awaiting_auth
        6. Execute in parallel waves
        7. Handle failures, skip downstream
        8. Return results
        """
        if not tasks:
            return WorkflowResult(status=WorkflowStatus.COMPLETED)
        
        task_map = {t.task_id: t for t in tasks}
        
        # Step 1: Check for unsupported executors
        unsupported_result = self._check_unsupported(tasks, ctx)
        if unsupported_result:
            return unsupported_result
        
        # Step 2: Preflight validation
        preflight_result = await self._preflight(tasks, ctx)
        if preflight_result:
            return preflight_result
        
        # Step 3: Authorization gate (before any money moves)
        # DEPRECATED: We allow tasks to run until they request authorization internally.
        # auth_result = self._check_authorization(tasks, ctx)
        # if auth_result:
        #     return auth_result
        
        # Step 4: Build execution waves
        waves = self._build_waves(tasks)
        
        # Step 5: Execute waves
        for wave in waves:
            # Filter out already-completed or skipped tasks
            # IMPORTANT: We MUST re-execute tasks that are 'awaiting_auth' or 'waiting_for_input'
            # because we are resuming them with new context (e.g. auth token or user input).
            executable = []
            for tid in wave:
                if tid not in ctx.task_results:
                    executable.append(task_map[tid])
                else:
                    status = ctx.task_results[tid].status
                    # If task is not in a terminal state, we re-execute it
                    if status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED):
                        executable.append(task_map[tid])
            
            if not executable:
                continue
            
            results = await self._execute_wave(executable, ctx)
            
            # Update context with results
            for result in results:
                ctx.set_task_result(result.task_id, result)
            
            # Check for failures that should skip downstream
            failed = [r for r in results if r.status == TaskStatus.FAILED]
            if failed:
                self._skip_downstream(failed, task_map, ctx)
            
            # Check for tasks awaiting authorization
            awaiting_auth = [r for r in results if r.status == TaskStatus.AWAITING_AUTH]
            if awaiting_auth:
                # If any task needs auth, we pause the workflow.
                # We prioritize the auth request.
                auth_task_ids = [r.task_id for r in awaiting_auth]
                
                # Get the message from the first auth task (usually confirmation summary)
                user_msg = None
                for r in awaiting_auth:
                    if r.data and r.data.get("response"):
                        user_msg = r.data["response"]
                        break
                        
                res = WorkflowResult.awaiting_auth(auth_task_ids)
                if user_msg:
                    res.user_message = user_msg
                
                # Attach task results so handler can access tokens/summaries
                res.task_results = ctx.task_results
                
                return res
        
        return WorkflowResult.complete(ctx.task_results)
    
    def _check_unsupported(
        self,
        tasks: list[PlannedTask],
        ctx: WorkflowContext,
    ) -> WorkflowResult | None:
        """Check for unsupported executors and build negotiation message."""
        unsupported: list[tuple[PlannedTask, str]] = []
        supported: list[PlannedTask] = []
        
        for task in tasks:
            handler = self.registry.get(task.executor)
            if isinstance(handler, UnsupportedExecutorResult):
                unsupported.append((task, handler.executor))
            else:
                supported.append(task)
        
        if not unsupported:
            return None
        
        # Build negotiation message
        unsupported_names = [exe for _, exe in unsupported]
        supported_desc = ", ".join(t.instruction for t in supported) if supported else None
        
        if supported:
            msg = f"I can {supported_desc}.\n\n"
            msg += f"However, **{unsupported_names[0]}** isn't available yet.\n\n"
            msg += "Would you like me to proceed with what I can do?"
        else:
            msg = f"**{unsupported_names[0].title()}** isn't available yet."
        
        # Mark unsupported as failed
        for task, exe in unsupported:
            ctx.set_task_result(
                task.task_id,
                TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error=f"Unsupported executor: {exe}",
                ),
            )
        
        return WorkflowResult(
            status=WorkflowStatus.WAITING_FOR_INPUT,
            task_results=ctx.task_results,
            user_message=msg,
        )
    
    async def _preflight(
        self,
        tasks: list[PlannedTask],
        ctx: WorkflowContext,
    ) -> WorkflowResult | None:
        """Run preflight validation on all tasks.
        
        For mixed intent flows, builds a comprehensive message showing:
        - What we CAN do (ready tasks)
        - What NEEDS INPUT (missing params)
        - What NEEDS NEGOTIATION (capability limits with alternatives)
        - What FAILED (hard rejections)
        """
        ready_tasks: list[PlannedTask] = []
        needs_input_tasks: list[tuple[PlannedTask, TaskResult]] = []
        negotiation_tasks: list[tuple[PlannedTask, TaskResult]] = []
        failed_tasks: list[tuple[PlannedTask, TaskResult]] = []
        
        for task in tasks:
            handler = self.registry.get(task.executor)
            if isinstance(handler, UnsupportedExecutorResult):
                continue  # Already handled in _check_unsupported
            
            result = await handler.validate(task, ctx)
            
            if result is None:
                ready_tasks.append(task)
            elif result.status == TaskStatus.NEEDS_INPUT:
                # Check if this is a negotiation (capability issue) vs missing input
                if result.data and result.data.get("negotiation"):
                    negotiation_tasks.append((task, result))
                else:
                    needs_input_tasks.append((task, result))
            elif result.status == TaskStatus.FAILED:
                failed_tasks.append((task, result))
        
        # If everything is ready, proceed
        if not needs_input_tasks and not negotiation_tasks and not failed_tasks:
            return None
        
        # Build comprehensive message for mixed intent flows
        msg_parts = []
        missing_by_task: dict[str, list[str]] = {}
        
        # Show what we CAN do
        if ready_tasks:
            can_do = "\n".join(f"  ✓ {t.instruction}" for t in ready_tasks)
            msg_parts.append(f"**I can do:**\n{can_do}")
        
        # Show what needs missing info
        if needs_input_tasks:
            needs_info = []
            for task, result in needs_input_tasks:
                missing_by_task[task.task_id] = result.missing_fields
                needs_info.append(f"  ⚠️ {task.instruction}\n     → {result.user_prompt}")
            msg_parts.append(f"**Need more info:**\n" + "\n".join(needs_info))
        
        # Show what needs negotiation (capability limits)
        if negotiation_tasks:
            negotiate = []
            for task, result in negotiation_tasks:
                missing_by_task[task.task_id] = result.missing_fields
                negotiate.append(f"  ⚠️ {task.instruction}\n     → {result.user_prompt}")
            msg_parts.append(f"**Feature not available:**\n" + "\n".join(negotiate))
        
        # Show hard failures
        if failed_tasks:
            fails = []
            for task, result in failed_tasks:
                fails.append(f"  ✗ {task.instruction}\n     → {result.error}")
            msg_parts.append(f"**Cannot do:**\n" + "\n".join(fails))
        
        # Add call to action
        if ready_tasks and (negotiation_tasks or needs_input_tasks):
            msg_parts.append("\nWould you like me to proceed with what I can do?")
        
        combined_msg = "\n\n".join(msg_parts)
        
        return WorkflowResult.waiting_for_input(missing_by_task, combined_msg)
    
    def _check_authorization(
        self,
        tasks: list[PlannedTask],
        ctx: WorkflowContext,
    ) -> WorkflowResult | None:
        """Check if any tasks require authorization."""
        if ctx.pin_verified:
            return None
        
        money_move_tasks = [
            t.task_id for t in tasks
            if requires_authorization(t.executor)
        ]
        
        if not money_move_tasks:
            return None
        
        return WorkflowResult.awaiting_auth(money_move_tasks)
    
    def _build_waves(self, tasks: list[PlannedTask]) -> list[list[str]]:
        """Build parallel execution waves respecting dependencies."""
        # Build dependency graph
        in_degree: dict[str, int] = {t.task_id: 0 for t in tasks}
        dependents: dict[str, list[str]] = defaultdict(list)
        
        for task in tasks:
            for dep in task.depends_on:
                if dep in in_degree:
                    in_degree[task.task_id] += 1
                    dependents[dep].append(task.task_id)
        
        # Kahn's algorithm for topological sort in waves
        waves: list[list[str]] = []
        ready = [tid for tid, deg in in_degree.items() if deg == 0]
        
        while ready:
            waves.append(ready)
            next_ready = []
            for tid in ready:
                for dep_tid in dependents[tid]:
                    in_degree[dep_tid] -= 1
                    if in_degree[dep_tid] == 0:
                        next_ready.append(dep_tid)
            ready = next_ready
        
        return waves
    
    async def _execute_wave(
        self,
        tasks: list[PlannedTask],
        ctx: WorkflowContext,
    ) -> list[TaskResult]:
        """Execute a wave of tasks with concurrency limit."""
        semaphore = asyncio.Semaphore(MAX_PARALLEL_TASKS)
        
        async def run_task(task: PlannedTask) -> TaskResult:
            async with semaphore:
                try:
                    handler = self.registry.get(task.executor)
                    if isinstance(handler, UnsupportedExecutorResult):
                        return handler.to_task_result(task.task_id)
                    
                    # Check condition if present
                    if task.condition and not self._evaluate_condition(task.condition, ctx):
                        return TaskResult(
                            task_id=task.task_id,
                            status=TaskStatus.SKIPPED,
                            error="Condition not met",
                        )
                    
                    return await handler.execute(task, ctx)
                except Exception as e:
                    logger.exception("task_execution_error", task_id=task.task_id)
                    return TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        error=str(e),
                    )
        
        results = await asyncio.gather(*[run_task(t) for t in tasks])
        return list(results)
    
    def _evaluate_condition(self, condition: str, ctx: WorkflowContext) -> bool:
        """Safely evaluate a condition expression."""
        # Build variable context from task results
        variables: dict[str, Any] = {}
        for task_id, result in ctx.task_results.items():
            variables[task_id] = result.data if result.data else {}
        
        try:
            # Safe subset of evaluation
            # Only allow: comparisons, boolean ops, attribute access
            return eval(condition, {"__builtins__": {}}, variables)
        except Exception:
            logger.warning("condition_eval_failed", condition=condition)
            return True  # Default to executing if condition fails
    
    def _skip_downstream(
        self,
        failed: list[TaskResult],
        task_map: dict[str, PlannedTask],
        ctx: WorkflowContext,
    ) -> None:
        """Mark downstream tasks as skipped when dependencies fail."""
        failed_ids = {r.task_id for r in failed}
        
        for task in task_map.values():
            if task.task_id in ctx.task_results:
                continue
            
            # Check if any dependency failed
            if any(dep in failed_ids for dep in task.depends_on):
                ctx.set_task_result(
                    task.task_id,
                    TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.SKIPPED,
                        error="Dependency failed",
                    ),
                )
                failed_ids.add(task.task_id)
