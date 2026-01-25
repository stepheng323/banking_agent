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
from shared.utils.network_utils import normalize_phone, resolve_network_from_phone

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

        elif data.recipient_name and not data.recipient_phone:
            matcher = BeneficiaryMatcher()
            beneficiaries = [Beneficiary(**b) for b in context.beneficiaries]

            status, single, candidates = matcher.match(data.recipient_name, beneficiaries)

            if status == "single" and single:
                patch["recipient_phone"] = single.account_number

                patch["recipient_name"] = single.account_name or single.alias or data.recipient_name
                if single.bank_code:
                    pass
            elif status == "clarify" and candidates:
                candidate_list = [
                    {"id": str(b.id), "label": f"{b.account_name or b.alias} • {b.account_number}"} for b in candidates
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

        current_phone = patch.get("recipient_phone") or data.recipient_phone

        if current_phone:
            try:
                formatted = normalize_phone(current_phone)
                network = resolve_network_from_phone(formatted)

                logger.info("network_inference", formatted=formatted, network=network)

                if formatted:
                    patch["recipient_phone"] = formatted

                if network:
                    patch["network"] = network

            except Exception as e:
                logger.warning("network_inference_failed", error=str(e))

        if patch:
            return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

        return TransactionResult(outcome=TransactionOutcome.OK)
