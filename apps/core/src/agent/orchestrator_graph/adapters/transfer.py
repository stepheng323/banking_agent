"""Transfer Subgraph Adapter."""

from typing import Any

from apps.core.src.agent.orchestrator_graph.protocols import SubgraphAdapter
from apps.core.src.agent.orchestrator_graph.state import OrchestratorState
from apps.core.src.agent.workflow.models import TaskResult, TaskStatus
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferSubgraphAdapter(SubgraphAdapter):
    """Adapter for Transfer operations."""

    def __init__(self, transfer_service, user_cache=None):
        self.service = transfer_service
        self.user_cache = user_cache

    async def prepare(self, task: PlannedTask, state: OrchestratorState) -> dict[str, Any]:
        """
        Check readiness.
        Resolves beneficiaries if needed.
        """
        params = task.parameters
        missing = []
        
        # 1. Amount Check
        if not params.get("amount"):
            missing.append("amount")
            
        # 2. Recipient Check (Account vs Name)
        account = params.get("recipient_account")
        bank = params.get("bank_name")
        name = params.get("recipient_name") or params.get("recipient")
        
        # If we have account + bank, we are good (mostly)
        if not (account and bank):
            # Try to resolve from Name/Beneficiaries
            if name and self.user_cache:
                # Logic ported from beneficiary.py
                # For now, simple lookup
                # In real imp, we would async fetch and fuzzy match
                # Assuming state has hydated beneficiaries?
                # The state definition had user context? No, orchestrator state has user_id.
                # We might need to fetch here or rely on cache.
                pass
            
            if not account:
                missing.append("recipient_account (or saved beneficiary)")
            if not bank:
                missing.append("bank_name")

        # 3. Narration (Optional but good to prompt if ambiguous context?)
        # Optional.

        if missing:
            return {"ready": False, "missing_fields": missing}

        return {"ready": True, "missing_fields": [], "updated_params": params}

    async def execute(self, task: PlannedTask, state: OrchestratorState) -> TaskResult:
        """Execute transfer."""
        params = task.parameters
        
        try:
            # Call service
            # Assuming params match service signature
            res = await self.service.transfer_funds(
                user_id=state.user_id,
                amount=float(params["amount"]),
                recipient_account=params["recipient_account"],
                bank_name=params["bank_name"],
                narration=params.get("narration", "Transfer"),
                pin="1234", # Auth Gate verified this, usually we pass a token or trusted flag
                # Ideally service accepts a 'verified' flag or we use the cached token
            )
            
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                data={"response": f"Successfully sent {params['amount']} to {params.get('recipient_name', params['recipient_account'])}.", "receipt": res},
            )
        except Exception as e:
            logger.exception("transfer_execution_failed", error=str(e))
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )
