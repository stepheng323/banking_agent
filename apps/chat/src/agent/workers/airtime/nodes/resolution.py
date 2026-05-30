"""Beneficiary and network resolution."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.chat.src.agent.workers.airtime.pipeline.base import AirtimeStep
from banking.beneficiaries.services.matcher import BeneficiaryMatcher
from banking.presentation.formatters.transaction_slot_prompts import format_transaction_slot_prompt
from banking.presentation.i18n.renderer import render_message
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone, resolve_network_from_phone

logger = get_logger(__name__)


def _mobile_beneficiary_network(beneficiary: Beneficiary) -> str | None:
    return (
        normalize_network_name(beneficiary.bank_name)
        or normalize_network_name(beneficiary.bank_code)
        or resolve_network_from_phone(beneficiary.account_number or "")
    )


def _is_mobile_beneficiary_record(raw: dict[str, Any]) -> bool:
    phone = normalize_nigerian_phone(str(raw.get("account_number") or ""))
    if not phone:
        return False

    beneficiary_type = str(raw.get("beneficiary_type") or "").strip().lower()
    if beneficiary_type in {"airtime", "data", "mobile"}:
        return True
    if beneficiary_type:
        return False

    # Legacy mobile beneficiaries stored the network in bank_name/bank_code.
    return bool(normalize_network_name(raw.get("bank_name")) or normalize_network_name(raw.get("bank_code")))


def _mobile_beneficiary_label(beneficiary: Beneficiary) -> str:
    phone = normalize_nigerian_phone(str(beneficiary.account_number or "")) or str(beneficiary.account_number or "")
    label = str(beneficiary.account_name or beneficiary.alias or phone).strip()
    network = _mobile_beneficiary_network(beneficiary)
    parts = [part for part in (label, phone, network) if part]
    return " • ".join(parts)


class ResolutionStep(AirtimeStep):
    """Resolves phone number and network."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates, worker_context
        locale = context.language
        patch = {}
        requested_network = normalize_network_name(data.network) if data.network else None

        if data.is_self and not data.recipient_phone:
            if context.phone_number:
                patch["recipient_phone"] = normalize_nigerian_phone(context.phone_number) or context.phone_number
                patch["recipient_name"] = "My Number"
                patch["is_self"] = True

        elif data.recipient_name and not data.recipient_phone:
            matcher = BeneficiaryMatcher()
            beneficiaries = [Beneficiary(**b) for b in context.beneficiaries if _is_mobile_beneficiary_record(b)]

            status, single, candidates = matcher.match(data.recipient_name, beneficiaries)

            if status == "single" and single:
                beneficiary_network = _mobile_beneficiary_network(single)
                if requested_network and beneficiary_network and beneficiary_network != requested_network:
                    prompt_payload = data.model_copy(update={"network": requested_network, "recipient_phone": None})
                    patch = data.model_dump(exclude_none=True)
                    patch.pop("recipient_phone", None)
                    patch.pop("beneficiary_id", None)
                    patch["network"] = requested_network
                    patch["is_self"] = False
                    return TransactionResult(
                        outcome=TransactionOutcome.NEEDS_INPUT,
                        required_fields=["recipient_phone"],
                        prompt=format_transaction_slot_prompt(
                            task_type="airtime",
                            payload=prompt_payload,
                            missing_fields=["recipient_phone"],
                            fallback_prompt=render_message("response.templates.ask_phone_number", locale),
                            locale=locale,
                        ),
                        patch=patch,
                        details={
                            "conflict": "NETWORK_BENEFICIARY_MISMATCH",
                            "beneficiary_network": beneficiary_network,
                            "provided_network": requested_network,
                        },
                    )
                patch["recipient_phone"] = normalize_nigerian_phone(str(single.account_number or "")) or single.account_number
                patch["recipient_name"] = single.account_name or single.alias or data.recipient_name
                if single.id:
                    patch["beneficiary_id"] = str(single.id)
                patch["is_self"] = False
                if network := beneficiary_network:
                    patch["network"] = network
            elif status == "clarify" and candidates:
                candidate_list = [{"id": str(b.id), "label": _mobile_beneficiary_label(b)} for b in candidates]
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["beneficiary_id"],
                    prompt=render_message("beneficiary.prompt.which_contact", locale),
                    details={
                        "ambiguity": "MULTIPLE_BENEFICIARIES",
                        "candidates": candidate_list,
                    },
                )
        elif not data.recipient_phone:
            default_phone = normalize_nigerian_phone(context.phone_number) or context.phone_number
            inferred_network = resolve_network_from_phone(default_phone)
            if default_phone and (not requested_network or not inferred_network or inferred_network == requested_network):
                patch["recipient_phone"] = default_phone
                patch["recipient_name"] = "My Number"
                patch["is_self"] = True

        current_phone = patch.get("recipient_phone") or data.recipient_phone

        if current_phone:
            try:
                formatted = normalize_nigerian_phone(str(current_phone))
                network = resolve_network_from_phone(str(current_phone))
                provided_network = normalize_network_name(data.network) if data.network else None
                default_phone = normalize_nigerian_phone(context.phone_number) or context.phone_number

                logger.info("network_inference", formatted=formatted, network=network)

                if formatted:
                    patch["recipient_phone"] = formatted
                    if data.is_self and default_phone and formatted != default_phone:
                        patch["is_self"] = False

                if network and provided_network and provided_network != network:
                    if patch.get("is_self") and requested_network:
                        patch.pop("recipient_phone", None)
                        patch.pop("recipient_name", None)
                        patch["is_self"] = False
                        current_phone = None
                    else:
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

                if network and current_phone:
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
