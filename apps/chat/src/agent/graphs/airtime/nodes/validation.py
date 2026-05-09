"""Airtime validation."""

from typing import Any

from apps.chat.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.chat.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import is_valid_nigerian_phone, normalize_network_name

logger = get_logger(__name__)


class ValidationStep(AirtimeStep):
    """Validates airtime data."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates, worker_context
        locale = context.language
        missing: list[str] = []
        required_fields: list[str] = []
        logger.info("Validating airtime data", data=data)

        if not data.recipient_phone:
            missing.append("phone number")
            required_fields.append("recipient_phone")

        if not data.amount:
            missing.append("amount")
            required_fields.append("amount")

        if missing:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=required_fields,
                prompt=render_message("airtime.validation.missing_fields", locale, {"missing": " and ".join(missing)}),
            )

        if data.recipient_phone and not is_valid_nigerian_phone(data.recipient_phone):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_phone"],
                prompt=render_message("airtime.validation.missing_fields", locale, {"missing": "phone number"}),
            )

        if data.amount <= 0:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["amount"],
                prompt=render_message("airtime.validation.amount_gt_zero", locale),
            )

        if data.amount > 50000:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["amount"],
                prompt=render_message("airtime.validation.max_amount", locale),
            )

        if data.network and not normalize_network_name(data.network):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["network"],
                prompt=render_message("response.templates.invalid_network", locale),
            )

        if not data.network:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["network"],
                prompt=render_message("response.templates.ask_network", locale, {"phone_masked": data.recipient_phone or ""}),
            )

        return TransactionResult(outcome=TransactionOutcome.OK)
