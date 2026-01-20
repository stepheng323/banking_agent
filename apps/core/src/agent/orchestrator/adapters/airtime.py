"""Airtime Subgraph Adapter."""

from typing import Any

from apps.core.src.agent.orchestrator.capabilities.airtime import (
    AirtimeCapability,
    decide_capability,
)
from apps.core.src.agent.orchestrator.models.domain import WorkerOutcome, WorkerResult
from apps.core.src.agent.orchestrator.protocols import SubgraphAdapter
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AirtimeSubgraphAdapter(SubgraphAdapter):
    """Adapter for Airtime operations."""

    def __init__(self, airtime_service, user_cache=None):
        self.service = airtime_service
        self.user_cache = user_cache

    async def prepare(self, task: PlannedTask, state: OrchestratorState) -> dict[str, Any]:
        """Check readiness and negotiate capabilities."""
        params = task.parameters
        user_input = state.last_message_text

        # Slot filling for phone number if missing
        recipient = (
            getattr(params, "recipient", None)
            or getattr(params, "recipient_phone", None)
            or getattr(params, "phone", None)
        )
        if not recipient and user_input:
            # Basic heuristic: if input looks like a phone number
            normalized = user_input.replace(" ", "").replace("-", "")
            if normalized.isdigit() and len(normalized) >= 10:
                if hasattr(params, "recipient_phone"):
                    params.recipient_phone = user_input
                elif hasattr(params, "phone"):
                    params.phone = user_input

        # Capability Check
        requires = []
        if getattr(params, "schedule", None) or getattr(params, "scheduled", None):
            requires.append(AirtimeCapability.SCHEDULED)
        if getattr(params, "recurring", None):
            requires.append(AirtimeCapability.RECURRING)

        # Default requirement (none explicit for basic airtime, but we can assume single)
        # requires.append(AirtimeCapability.SINGLE_PURCHASE) # If such cap exists?
        # AirtimeCapability enum might vary. Let's assume empty requires is fine if no special features.

        decision = decide_capability(requires)

        if not decision.allowed:
            is_affirmation = user_input and user_input.lower() in ["yes", "proceed", "okay", "go ahead", "do it"]

            if is_affirmation and decision.suggested_action:
                logger.info("capability_negotiation_accepted", action=decision.suggested_action)
                if decision.patch:
                    for k, v in decision.patch.items():
                        setattr(params, k, v)

                # Clear unsupported flags
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

        # 3. Delegate to Subgraph Preflight for Enrichment & Validation
        try:
            params_dict = params.model_dump()
            preflight_result = await self.service.preflight(
                phone=state.phone_number,
                text=user_input or "",
                params=params_dict,
            )

            # Update params from enrichment
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
            logger.error("airtime_adapter_preflight_failed", error=str(e))
            # Fallback to basic checks
            missing = []
            if not getattr(params, "amount", None):
                missing.append("amount")
            if not (getattr(params, "recipient_phone", None) or getattr(params, "phone", None)):
                missing.append("recipient_phone")
            if missing:
                return {"ready": False, "missing_fields": missing, "updated_params": params}

        return {"ready": True, "missing_fields": [], "updated_params": params}

    async def execute(self, task: PlannedTask, state: OrchestratorState) -> WorkerResult:
        """Execute airtime task."""
        try:
            logger.info("executing_airtime_task", task_id=task.task_id)

            user_id = state.phone_number

            # Call service using run_simple (facade)
            classification = {"primary_intent": "airtime", "confidence": 1.0}

            response_text = await self.service.run_simple(
                phone=user_id, text=state.last_message_text or "", classification_result=classification
            )

            return WorkerResult(outcome=WorkerOutcome.SUCCESS, data={"response": response_text})
        except Exception as e:
            logger.error("airtime_execution_failed", error=str(e))
            return WorkerResult(outcome=WorkerOutcome.FAILED, error=str(e))
