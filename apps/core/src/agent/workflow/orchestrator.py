"""Workflow orchestrator handler.

Integrates WorkflowDAGExecutor with the orchestrator pipeline.
Replaces the sequential task queue with DAG-based execution.
"""

from typing import TYPE_CHECKING, Any
import uuid

from shared.types.planner import PlannedTask, PlannerOutput
from shared.utils.logging import get_logger

from apps.core.src.agent.workflow import (
    WorkflowDAGExecutor,
    WorkflowContext,
    WorkflowStatus,
    WorkflowResult,
    WorkflowPersistence,
    check_aggregate_limits,
    create_workflow_executor,
)

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
    from shared.cache.user_data import UserDataCache

logger = get_logger(__name__)


class WorkflowOrchestrator:
    """
    Orchestrates workflow execution for multi-task requests.
    
    Flow:
    1. Receives PlannerOutput with tasks
    2. Creates WorkflowContext with user data
    3. Checks aggregate limits
    4. Executes via DAG executor
    5. Handles auth gates and input collection
    6. Returns response to user
    """
    
    def __init__(
        self,
        executor: WorkflowDAGExecutor,
        persistence: WorkflowPersistence | None = None,
        user_cache: "UserDataCache | None" = None,
    ):
        self.executor = executor
        self.persistence = persistence
        self.user_cache = user_cache
    
    async def execute_workflow(
        self,
        phone_number: str,
        planner_output: PlannerOutput,
        pin_verified: bool = False,
    ) -> WorkflowResult:
        """
        Execute a workflow from planner output.
        
        Args:
            phone_number: User's phone number (used as identifier)
            planner_output: Output from planner with tasks
            pin_verified: Whether PIN has been verified
        
        Returns:
            WorkflowResult with status and results/messages
        """
        tasks = planner_output.tasks
        
        if not tasks:
            return WorkflowResult(status=WorkflowStatus.COMPLETED)
        
        # Generate workflow ID
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        
        # Build context
        ctx = await self._build_context(phone_number, workflow_id, pin_verified)
        
        # Check aggregate limits before execution
        limit_result = check_aggregate_limits(tasks)
        if not limit_result.allowed:
            return WorkflowResult(
                status=WorkflowStatus.FAILED,
                user_message=limit_result.to_user_message(),
            )
        
        # Execute via DAG
        result = await self.executor.execute(tasks, ctx)
        
        # Inject workflow_id into result
        result.workflow_id = workflow_id
        
        # Persist if waiting for input or auth
        logger.info("workflow_persist_check", has_persistence=bool(self.persistence), status=result.status.value)
        if self.persistence and result.status in (
            WorkflowStatus.WAITING_FOR_INPUT,
            WorkflowStatus.AWAITING_AUTH,
        ):
            logger.info("workflow_persisting", workflow_id=workflow_id, phone=phone_number)
            await self._persist_workflow(workflow_id, tasks, ctx, result)
        
        # Build user-facing response
        result.user_message = self._build_response_message(result, tasks)
        
        return result
    
    async def resume_workflow(
        self,
        phone_number: str,
        additional_input: dict[str, Any] | None = None,
        pin_verified: bool = False,
    ) -> WorkflowResult | None:
        """
        Resume a paused workflow after user input or auth.
        
        Returns None if no active workflow to resume.
        """
        if not self.persistence:
            return None
        
        workflow_id = await self.persistence.get_active_workflow_id(phone_number)
        if not workflow_id:
            return None
        
        state = await self.persistence.load(phone_number, workflow_id)
        if not state:
            return None
        
        # Only resume awaiting_auth workflows - other states (waiting_for_input, collecting_amount)
        # should be cleared and let user start fresh
        if state.status != "awaiting_auth":
            logger.info("resume_skipping_non_auth", status=state.status, workflow_id=workflow_id)
            await self.persistence.clear(phone_number, workflow_id)
            return None
        
        # Rebuild context
        ctx = await self._build_context(
            phone_number, workflow_id, pin_verified or state.pin_verified
        )
        
        # Apply previous results
        for task_id, result_data in state.task_results.items():
            from apps.core.src.agent.workflow.models import TaskResult, TaskStatus
            ctx.set_task_result(
                task_id,
                TaskResult(
                    task_id=task_id,
                    status=TaskStatus(result_data.get("status", "completed")),
                    data=result_data.get("data", {}),
                ),
            )
        
        # Rebuild pending tasks
        tasks = [PlannedTask(**t) for t in state.pending_tasks]
        
        # Apply additional input to context (e.g., user's new message for mid-flow updates)
        # The handler will check ctx.additional_input for user_text
        if additional_input:
            ctx.additional_input = additional_input
        
        # Execute remaining
        result = await self.executor.execute(tasks, ctx)
        
        # Inject workflow_id into result
        result.workflow_id = workflow_id
        
        # Clear if completed
        if result.status in (WorkflowStatus.COMPLETED, WorkflowStatus.PARTIAL_SUCCESS, WorkflowStatus.FAILED):
            await self.persistence.clear(phone_number, workflow_id)
        else:
            await self._persist_workflow(workflow_id, tasks, ctx, result)
        
        result.user_message = self._build_response_message(result, tasks)
        return result
    
    async def _build_context(
        self,
        phone_number: str,
        workflow_id: str,
        pin_verified: bool,
    ) -> WorkflowContext:
        """Build workflow context with user data."""
        ctx = WorkflowContext(
            user_id=phone_number,  # Use phone_number as user identifier
            phone_number=phone_number,
            pin_verified=pin_verified,
            workflow_id=workflow_id,
        )
        
        # Hydrate with user data if cache available
        if self.user_cache:
            try:
                cache_data = await self.user_cache.get_all_user_data(phone_number)
                ctx.user_profile = cache_data.get("profile", {})
                ctx.accounts = cache_data.get("accounts", [])
                ctx.beneficiaries = cache_data.get("beneficiaries", [])
                logger.info("workflow_context_loaded", phone=phone_number, beneficiary_count=len(ctx.beneficiaries))
            except Exception as e:
                logger.warning("user_cache_error", error=str(e))
        else:
            logger.warning("user_cache_missing", phone=phone_number)
        
        return ctx
    
    async def _persist_workflow(
        self,
        workflow_id: str,
        tasks: list[PlannedTask],
        ctx: WorkflowContext,
        result: WorkflowResult,
    ) -> None:
        """Persist workflow state for resumption."""
        from apps.core.src.agent.workflow.persistence import PersistedWorkflowState
        
        # Determine pending tasks (not completed, failed, or skipped)
        completed = {tid for tid, r in ctx.task_results.items() if r.status.value == "completed"}
        failed = {tid for tid, r in ctx.task_results.items() if r.status.value == "failed"}
        skipped = {tid for tid, r in ctx.task_results.items() if r.status.value == "skipped"}
        
        pending_tasks = [
            t.model_dump() for t in tasks
            if t.task_id not in completed and t.task_id not in failed and t.task_id not in skipped
        ]
        
        logger.info(
            "persist_workflow_debug",
            total_tasks=len(tasks),
            completed=list(completed),
            failed=list(failed),
            skipped=list(skipped),
            pending_count=len(pending_tasks),
            task_result_statuses={tid: r.status.value for tid, r in ctx.task_results.items()},
        )
        
        from apps.core.src.agent.workflow.persistence import PersistedWorkflowState, ActiveWorkflowMetadata
        from apps.core.src.agent.workflow.models import InterruptPolicy, WorkflowStatus
        
        # Determine policy
        # We use ALLOW for AWAITING_AUTH so that "corrections" (e.g. "Its for launch") 
        # or "support" queries can be routed to the Planner/Router.
        # The Planner is context-aware and will reject/queue new money ops if unsafe.
        policy = InterruptPolicy.ALLOW
        if result.status == WorkflowStatus.WAITING_FOR_INPUT:
            policy = InterruptPolicy.CONFIRM
            
        # Determine expected inputs
        expected_input = []
        if result.status == WorkflowStatus.WAITING_FOR_INPUT:
            for fields in result.missing_by_task.values():
                expected_input.extend(fields)
        
        # Determine graph/executor from first pending task
        graph_name = None
        if pending_tasks:
            graph_name = pending_tasks[0].get("executor")
            
        active_metadata = ActiveWorkflowMetadata(
            workflow_id=workflow_id,
            status=result.status.value,
            current_task_ids=[t["task_id"] for t in pending_tasks],
            expected_input=expected_input,
            graph_name=graph_name,
            interrupt_policy=policy,
        )
        
        state = PersistedWorkflowState(
            workflow_id=workflow_id,
            user_id=ctx.user_id,
            phone_number=ctx.phone_number,
            pin_verified=ctx.pin_verified,
            pending_tasks=pending_tasks,
            completed_task_ids=list(completed),
            failed_task_ids=list(failed),
            skipped_task_ids=list(skipped),
            task_results={tid: r.model_dump() for tid, r in ctx.task_results.items()},
            status=result.status.value,
            missing_by_task=result.missing_by_task,
            pending_auth_tasks=result.pending_auth_tasks,
            active_metadata=active_metadata,
            user_message=result.user_message,
        )
        
        await self.persistence.save(ctx.user_id, workflow_id, state)
    
    def _build_response_message(self, result: WorkflowResult, tasks: list[PlannedTask]) -> str:
        """Build user-facing response message."""
        if result.user_message:
            return result.user_message
        
        if result.status == WorkflowStatus.COMPLETED:
            if len(tasks) == 1:
                return ""  # Single task, let the handler's response through
            return f"✅ All {len(tasks)} tasks completed successfully."
        
        if result.status == WorkflowStatus.PARTIAL_SUCCESS:
            completed = sum(1 for r in result.task_results.values() if r.status.value == "completed")
            failed = sum(1 for r in result.task_results.values() if r.status.value == "failed")
            return f"✅ {completed} completed, ❌ {failed} failed."
        
        if result.status == WorkflowStatus.AWAITING_AUTH:
            return "Please enter your PIN to authorize."
        
        if result.status == WorkflowStatus.FAILED:
            return "Something went wrong with your request."
        
        return ""
