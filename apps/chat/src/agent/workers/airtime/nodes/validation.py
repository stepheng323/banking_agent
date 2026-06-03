"""Airtime validation."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.chat.src.agent.workers.airtime.pipeline.base import AirtimeStep
from banking.presentation.formatters.transaction_slot_prompts import format_transaction_slot_prompt
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import is_valid_nigerian_phone, normalize_network_name

logger = get_logger(__name__)


def _missing_field_label(field: str, locale: str) -> str:
    if field == "recipient_phone":
        return render_message("airtime.validation.field.phone_number", locale)
    if field == "amount":
        return render_message("airtime.validation.field.amount", locale)
    return field


def _join_missing_fields(fields: list[str], locale: str) -> str:
    labels = [_missing_field_label(field, locale) for field in fields]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return render_message("airtime.validation.join_two", locale, {"first": labels[0], "second": labels[1]})
    return render_message(
        "airtime.validation.join_many",
        locale,
        {"head": ", ".join(labels[:-1]), "last": labels[-1]},
    )


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
            missing.append("recipient_phone")
            required_fields.append("recipient_phone")

        if not data.amount:
            missing.append("amount")
            required_fields.append("amount")

        if missing:
            fallback_prompt = render_message(
                "airtime.validation.missing_fields",
                locale,
                {"missing": _join_missing_fields(missing, locale)},
            )
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=required_fields,
                prompt=format_transaction_slot_prompt(
                    task_type="airtime",
                    payload=data,
                    missing_fields=required_fields,
                    fallback_prompt=fallback_prompt,
                    locale=locale,
                ),
            )

        assert data.amount is not None
        if data.recipient_phone and not is_valid_nigerian_phone(data.recipient_phone):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_phone"],
                prompt=render_message("airtime.validation.invalid_phone", locale),
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
                prompt=render_message(
                    "response.templates.ask_network", locale, {"phone_masked": data.recipient_phone or ""}
                ),
            )

        return TransactionResult(outcome=TransactionOutcome.OK)
