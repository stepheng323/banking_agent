"""Data Subgraph Adapter."""

from typing import Any

from apps.core.src.agent.orchestrator.capabilities.data import (
    DataCapability,
    decide_capability,
)
from apps.core.src.agent.orchestrator.models.domain import WorkerOutcome, WorkerResult
from apps.core.src.agent.orchestrator.protocols import SubgraphAdapter
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataSubgraphAdapter(SubgraphAdapter):
    """Adapter for Data Purchase operations."""

    def __init__(self, data_service, user_cache=None):
        self.service = data_service
        self.user_cache = user_cache

    async def prepare(self, task: PlannedTask, state: OrchestratorState) -> dict[str, Any]:
        """Check readiness and negotiate capabilities."""
        params = task.parameters
        user_input = state.last_message_text

        recipient = (
            getattr(params, "recipient", None)
            or getattr(params, "recipient_phone", None)
            or getattr(params, "phone", None)
        )
        if not recipient and user_input:
            normalized = user_input.replace(" ", "").replace("-", "")
            if normalized.isdigit() and len(normalized) >= 10:
                if hasattr(params, "recipient_phone"):
                    params.recipient_phone = user_input

        requires = []
        if getattr(params, "schedule", None) or getattr(params, "scheduled", None):
            requires.append(DataCapability.SCHEDULED)
        if getattr(params, "recurring", None):
            requires.append(DataCapability.RECURRING)

        decision = decide_capability(requires)

        if not decision.allowed:
            is_affirmation = user_input and user_input.lower() in ["yes", "proceed", "okay", "go ahead", "do it"]

            if is_affirmation and decision.suggested_action:
                logger.info("capability_negotiation_accepted", action=decision.suggested_action)
                if decision.patch:
                    for k, v in decision.patch.items():
                        setattr(params, k, v)

                for flag in ["schedule", "scheduled", "recurring"]:
                    if hasattr(params, flag):
                        delattr(params, flag)
            else:
                return {
                    "ready": False,
                    "missing_fields": ["negotiation"],
                    "question": decision.prompt,
                    "updated_params": params,
                }

        try:
            params_dict = params.model_dump()
            preflight_result = await self.service.preflight(
                phone=state.phone_number,
                text=user_input or "",
                params=params_dict,
            )

            if "enriched_params" in preflight_result:
                enriched = preflight_result["enriched_params"]
                for k, v in enriched.items():
                    if v is not None:
                        setattr(params, k, v)

            if not preflight_result.get("ready", False):
                return {
                    "ready": False,
                    "missing_fields": preflight_result.get("missing_fields", []),
                    "question": preflight_result.get("question"),
                    "updated_params": params,
                }

        except Exception as e:
            logger.error("data_adapter_preflight_failed", error=str(e))
            # Fallback
            missing = []
            if not getattr(params, "amount", None) and not getattr(params, "budget", None):
                missing.append("amount")
            if not (
                getattr(params, "target_phone", None)
                or getattr(params, "phone", None)
                or getattr(params, "recipient_phone", None)
            ):
                missing.append("recipient_phone")
            if missing:
                return {"ready": False, "missing_fields": missing, "updated_params": params}

        return {"ready": True, "missing_fields": [], "updated_params": params}

    async def execute(self, task: PlannedTask, state: OrchestratorState) -> WorkerResult:
        """Execute data task."""
        try:
            logger.info("executing_data_task", task_id=task.task_id)
            user_id = state.phone_number

            # Call service using run_simple (facade)
            classification = {"primary_intent": "data_purchase", "confidence": 1.0}

            response_text = await self.service.run_simple(
                phone=user_id, text=state.last_message_text or "", classification_result=classification
            )

            return WorkerResult(outcome=WorkerOutcome.SUCCESS, data={"response": response_text})
        except Exception as e:
            logger.error("data_execution_failed", error=str(e))
            return WorkerResult(outcome=WorkerOutcome.FAILED, error=str(e))
