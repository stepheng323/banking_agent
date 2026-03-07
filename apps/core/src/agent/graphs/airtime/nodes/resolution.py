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
from shared.i18n import render_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone, resolve_network_from_phone

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
        locale = context.language
        patch = {}

        if data.is_self and not data.recipient_phone:
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
                    prompt=render_message("beneficiary.prompt.which_contact", locale),
                    details={
                        "ambiguity": "MULTIPLE_BENEFICIARIES",
                        "candidates": candidate_list,
                    },
                )

        current_phone = patch.get("recipient_phone") or data.recipient_phone

        if current_phone:
            try:
                formatted = normalize_nigerian_phone(str(current_phone))
                network = resolve_network_from_phone(str(current_phone))
                provided_network = normalize_network_name(data.network) if data.network else None

                logger.info("network_inference", formatted=formatted, network=network)

                if formatted:
                    patch["recipient_phone"] = formatted

                if network and provided_network and provided_network != network:
                    return TransactionResult(
                        outcome=TransactionOutcome.NEEDS_INPUT,
                        required_fields=["network"],
                        prompt=render_message(
                            "response.templates.ask_network",
                            locale,
                            {"phone_masked": formatted or str(current_phone)},
                        ),
                        update_message=render_message("response.templates.invalid_network", locale),
                        details={
                            "conflict": "NETWORK_PHONE_MISMATCH",
                            "inferred_network": network,
                            "provided_network": provided_network,
                        },
                    )

                if network:
                    patch["network"] = network

            except Exception as e:
                logger.warning("network_inference_failed", error=str(e))

        if not patch.get("network") and data.network:
            normalized_network = normalize_network_name(data.network)
            if normalized_network:
                patch["network"] = normalized_network

        if patch:
            return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

        return TransactionResult(outcome=TransactionOutcome.OK)
