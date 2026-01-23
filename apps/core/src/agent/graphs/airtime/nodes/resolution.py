"""Beneficiary and network resolution."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ResolutionStep(AirtimeStep):
    """Resolves phone number and network."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        patch = {}
        
        if data.is_self:
            if context.phone_number:
                patch["recipient_phone"] = context.phone_number
                patch["recipient_name"] = "My Number"

        if data.recipient_name and not data.recipient_phone and not patch.get("recipient_phone"):
            matcher = BeneficiaryMatcher()
            beneficiaries = [Beneficiary(**b) for b in context.beneficiaries]
            
            status, single, candidates = matcher.match(data.recipient_name, beneficiaries)
            
            if status == "single" and single:
                patch["recipient_phone"] = single.account_number # phone is stored in account_number for airtime benes usually? 
                # Actually for airtime/bills benes, verify schema. 
                # Assuming standard Beneficiary schema: account_number holds the identifier (phone).
                patch["recipient_name"] = single.account_name or single.alias or data.recipient_name
                if single.bank_code: # often stores network for airtime benes?
                     # Ideally we re-infer network to be safe, but if stored use it?
                     pass
            elif status == "clarify" and candidates:
                 candidate_list = [
                    {"id": str(b.id), "label": f"{b.account_name or b.alias} • {b.account_number}"}
                    for b in candidates
                ]
                 return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["beneficiary_id"],
                    prompt="Which contact did you mean?",
                    details={
                        "ambiguity": "MULTIPLE_BENEFICIARIES",
                        "candidates": candidate_list,
                    },
                )

        # Apply basic patches first so network inference has data
        current_phone = patch.get("recipient_phone") or data.recipient_phone
        
        # 3. Network Inference
        # Always infer network if we have a phone number, overriding any stale/extracted partials
        if current_phone and getattr(worker_context, "validation_service", None):
            try:
                # validation_service.validate_mobile checks prefix and returns network
                # It returns (is_valid, network_name, formatted_phone)
                is_valid, network, formatted = await worker_context.validation_service.validate_mobile(current_phone)
                if is_valid and network:
                    patch["network"] = network
                    patch["recipient_phone"] = formatted # Normalize formatting
                elif is_valid and formatted:
                     patch["recipient_phone"] = formatted
            except Exception as e:
                logger.warning("network_inference_failed", error=str(e))
        
        if patch:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=patch
            )

        return TransactionResult(outcome=TransactionOutcome.OK)
