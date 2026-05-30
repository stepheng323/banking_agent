from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.workers.data.pipeline.base import PipelineStep, continue_pipeline
from banking.beneficiaries.services.matcher import BeneficiaryMatcher
from banking.presentation.i18n.renderer import render_message
from shared.database.models import Beneficiary
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone, resolve_network_from_phone


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

    return bool(normalize_network_name(raw.get("bank_name")) or normalize_network_name(raw.get("bank_code")))


def _mobile_beneficiary_label(beneficiary: Beneficiary) -> str:
    phone = normalize_nigerian_phone(str(beneficiary.account_number or "")) or str(beneficiary.account_number or "")
    label = str(beneficiary.account_name or beneficiary.alias or phone).strip()
    network = _mobile_beneficiary_network(beneficiary)
    parts = [part for part in (label, phone, network) if part]
    return " • ".join(parts)


def _extracted_recipient_name(payload: DataPayload) -> str | None:
    if payload.recipient_name:
        return payload.recipient_name
    if payload.extraction and payload.extraction.entities.recipient_name:
        return payload.extraction.entities.recipient_name
    return None


class ResolutionStep(PipelineStep):
    """Resolution Step: Resolve ambiguities in network, plan, or target."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult:
        del gates, worker_context
        locale = context.language
        requested_network = normalize_network_name(payload.network) if payload.network else None
        if payload.network and not requested_network:
            requested_network = payload.network.strip().upper()

        # 1. Resolve Target Phone
        if not payload.target_phone:
            if payload.extraction and payload.extraction.entities.recipient_phone:
                payload.target_phone = payload.extraction.entities.recipient_phone
                payload.is_self = False
            elif recipient_name := _extracted_recipient_name(payload):
                payload.recipient_name = recipient_name
                matcher = BeneficiaryMatcher()
                beneficiaries = [Beneficiary(**b) for b in context.beneficiaries if _is_mobile_beneficiary_record(b)]
                status, single, candidates = matcher.match(recipient_name, beneficiaries)

                if status == "single" and single:
                    beneficiary_network = _mobile_beneficiary_network(single)
                    if requested_network and beneficiary_network and beneficiary_network != requested_network:
                        return TransactionResult(
                            outcome=TransactionOutcome.NEEDS_INPUT,
                            required_fields=["target_phone"],
                            prompt=render_message("data.resolve.ask_target_phone", locale),
                            patch=payload.model_dump(exclude_none=True),
                            details={
                                "conflict": "NETWORK_BENEFICIARY_MISMATCH",
                                "beneficiary_network": beneficiary_network,
                                "provided_network": requested_network,
                            },
                        )

                    payload.target_phone = normalize_nigerian_phone(str(single.account_number or "")) or single.account_number
                    payload.recipient_name = single.account_name or single.alias or recipient_name
                    if single.id:
                        payload.beneficiary_id = str(single.id)
                    payload.is_self = False
                    if beneficiary_network and not payload.network:
                        payload.network = beneficiary_network
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
            elif payload.extraction and payload.extraction.entities.is_self:
                default_phone = normalize_nigerian_phone(context.phone_number) or context.phone_number
                inferred_network = resolve_network_from_phone(default_phone)
                if not requested_network or not inferred_network or inferred_network == requested_network:
                    payload.target_phone = default_phone
                    payload.is_self = True
            else:
                default_phone = normalize_nigerian_phone(context.phone_number) or context.phone_number
                inferred_network = resolve_network_from_phone(default_phone)
                if not requested_network or not inferred_network or inferred_network == requested_network:
                    payload.target_phone = default_phone
                    payload.is_self = bool(payload.target_phone)

        if not payload.target_phone:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["target_phone"],
                prompt=render_message("data.resolve.ask_target_phone", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        normalized_phone = normalize_nigerian_phone(str(payload.target_phone))
        if normalized_phone:
            payload.target_phone = normalized_phone

        # 2. Resolve Network
        normalized_network = normalize_network_name(payload.network) if payload.network else None
        if normalized_network:
            payload.network = normalized_network
        elif payload.network:
            payload.network = payload.network.strip().upper()

        if not payload.network:
            inferred_network = resolve_network_from_phone(payload.target_phone)
            if inferred_network:
                payload.network = inferred_network

        if not payload.network:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["network"],
                prompt=render_message("data.resolve.ask_network", locale, {"target_phone": payload.target_phone}),
                patch=payload.model_dump(exclude_none=True),
            )

        # 3. Resolve Plan (if extracted amount/size)
        # This implies we might need a PlanSelection step or handle it here.
        # Ideally, we present a list of plans if 'plan_code' is missing but 'amount' is present.

        # ...Logic to fetch plans and match...

        return continue_pipeline(payload)
