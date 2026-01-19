"""Workflow persistence for recovery and retry.

Stores workflow state in Redis for resumption after:
- Authorization (PIN verification)
- User input (missing fields)
- System restart
"""

import json
from typing import Any

from pydantic import BaseModel, Field

from shared.utils.logging import get_logger

from apps.core.src.agent.workflow.models import InterruptPolicy

logger = get_logger(__name__)

# Redis key patterns
WORKFLOW_KEY = "workflow:{user_id}:{workflow_id}"
ACTIVE_WORKFLOW_KEY = "user:{user_id}:active_workflow"
WORKFLOW_TTL = 3600  # 1 hour


class ActiveWorkflowMetadata(BaseModel):
    """Lightweight metadata for active workflow checks."""
    
    workflow_id: str
    status: str
    current_task_ids: list[str]
    expected_input: list[str] = Field(default_factory=list)
    graph_name: str | None = None
    interrupt_policy: InterruptPolicy = InterruptPolicy.ALLOW
    last_prompt_message_id: str | None = None


class PersistedWorkflowState(BaseModel):
    """Serializable workflow state for Redis persistence."""
    
    workflow_id: str
    user_id: str
    phone_number: str
    pin_verified: bool = False
    
    # Task states
    pending_tasks: list[dict[str, Any]]  # Serialized PlannedTask
    completed_task_ids: list[str]
    failed_task_ids: list[str]
    skipped_task_ids: list[str]
    
    # Results
    task_results: dict[str, dict[str, Any]]  # task_id → serialized TaskResult
    
    # State
    status: str  # WorkflowStatus value
    missing_by_task: dict[str, list[str]]
    pending_auth_tasks: list[str]
    
    # Session Gate Metadata
    active_metadata: ActiveWorkflowMetadata | None = None
    
    # User message to display on resume
    user_message: str | None = None


class WorkflowPersistence:
    """Handles workflow state persistence in Redis."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def save(
        self,
        user_id: str,
        workflow_id: str,
        state: PersistedWorkflowState,
    ) -> None:
        """Save workflow state to Redis."""
        key = WORKFLOW_KEY.format(user_id=user_id, workflow_id=workflow_id)
        active_key = ACTIVE_WORKFLOW_KEY.format(user_id=user_id)
        
        try:
            data = state.model_dump_json()
            await self.redis.set(key, data, ex=WORKFLOW_TTL)
            
            # Store active metadata if present, otherwise fallback to ID (legacy)
            if state.active_metadata:
                active_data = state.active_metadata.model_dump_json()
                await self.redis.set(active_key, active_data, ex=WORKFLOW_TTL)
            else:
                await self.redis.set(active_key, workflow_id, ex=WORKFLOW_TTL)
            
            logger.info(
                "workflow_state_saved",
                user_id=user_id,
                workflow_id=workflow_id,
                status=state.status,
            )
        except Exception as e:
            logger.exception("workflow_save_error", user_id=user_id, workflow_id=workflow_id)
            raise
    
    async def load(self, user_id: str, workflow_id: str) -> PersistedWorkflowState | None:
        """Load workflow state from Redis."""
        key = WORKFLOW_KEY.format(user_id=user_id, workflow_id=workflow_id)
        
        try:
            data = await self.redis.get(key)
            if not data:
                return None
            
            return PersistedWorkflowState.model_validate_json(data)
        except Exception as e:
            logger.exception("workflow_load_error", user_id=user_id, workflow_id=workflow_id)
            return None
    
    async def get_active_workflow_id(self, user_id: str) -> str | None:
        """Get the active workflow ID for a user."""
        key = ACTIVE_WORKFLOW_KEY.format(user_id=user_id)
        
        try:
            val = await self.redis.get(key)
            if not val:
                return None
            
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            
            # Try to parse as JSON metadata
            try:
                # If it's a JSON object, extract ID
                if val.strip().startswith("{"):
                    meta = json.loads(val)
                    return meta.get("workflow_id")
                # Legacy: plain string ID
                return val
            except json.JSONDecodeError:
                return val
                
        except Exception:
            return None

    async def get_active_workflow_metadata(self, user_id: str) -> ActiveWorkflowMetadata | None:
        """Get the full active workflow metadata."""
        key = ACTIVE_WORKFLOW_KEY.format(user_id=user_id)
        
        try:
            val = await self.redis.get(key)
            if not val:
                return None
            
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            
            # Try to parse as JSON metadata
            if val.strip().startswith("{"):
                return ActiveWorkflowMetadata.model_validate_json(val)
            
            return None
        except Exception:
            return None
    
    async def clear(self, user_id: str, workflow_id: str) -> None:
        """Clear workflow state from Redis."""
        key = WORKFLOW_KEY.format(user_id=user_id, workflow_id=workflow_id)
        active_key = ACTIVE_WORKFLOW_KEY.format(user_id=user_id)
        
        try:
            await self.redis.delete(key)
            
            # Only clear active key if it matches
            current_active = await self.redis.get(active_key)
            if current_active == workflow_id:
                await self.redis.delete(active_key)
            
            logger.info("workflow_state_cleared", user_id=user_id, workflow_id=workflow_id)
        except Exception as e:
            logger.warning("workflow_clear_error", user_id=user_id, workflow_id=workflow_id, error=str(e))
    
    async def has_active_workflow(self, user_id: str) -> bool:
        """Check if user has an active workflow."""
        workflow_id = await self.get_active_workflow_id(user_id)
        if not workflow_id:
            return False
        
        state = await self.load(user_id, workflow_id)
        return state is not None and state.status in ("waiting_for_input", "awaiting_auth")
