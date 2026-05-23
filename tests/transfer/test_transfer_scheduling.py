from apps.chat.src.agent.graphs.transfer.models.types import TransferContext, TransferGates, TransferPayload
from apps.chat.src.agent.graphs.transfer.nodes.confirmation import build_confirmation
from apps.chat.src.agent.graphs.transfer.worker import ScheduleRequirementsStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from shared.formatters.transaction_copy import build_confirmation_header


async def test_date_only_scheduled_transfer_asks_for_time_before_confirmation() -> None:
    step = ScheduleRequirementsStep()
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="0034575515",
        recipient_bank_name="Gtb",
        schedule_mode="one_time",
        recurrence_type="one_time",
        schedule_start_date="2026-05-22",
    )

    result = await step.execute(
        payload,
        TransferContext(phone_number="2348000000999", language="en"),
        TransferGates(),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["schedule_time_local"]
    assert result.prompt == "What time should I schedule it?"


async def test_scheduled_transfer_with_date_and_time_reaches_confirmation() -> None:
    step = ScheduleRequirementsStep()
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="0034575515",
        recipient_bank_name="Gtb",
        schedule_mode="one_time",
        recurrence_type="one_time",
        schedule_start_date="2026-05-22",
        schedule_time_local="08:00",
    )

    result = await step.execute(
        payload,
        TransferContext(phone_number="2348000000999", language="en"),
        TransferGates(),
    )

    assert result.outcome == TransactionOutcome.OK


async def test_monthly_scheduled_transfer_without_time_asks_only_for_time() -> None:
    step = ScheduleRequirementsStep()
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="0034575515",
        recipient_bank_name="Gtb",
        schedule_mode="recurring",
        recurrence_type="monthly",
    )

    result = await step.execute(
        payload,
        TransferContext(phone_number="2348000000999", language="en"),
        TransferGates(),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["schedule_time_local"]
    assert result.prompt == "What time should I schedule it?"


async def test_scheduled_transfer_time_prompt_uses_locale_catalog() -> None:
    step = ScheduleRequirementsStep()
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="0034575515",
        recipient_bank_name="Gtb",
        schedule_mode="one_time",
        recurrence_type="one_time",
        schedule_start_date="2026-05-22",
    )

    result = await step.execute(
        payload,
        TransferContext(phone_number="2348000000999", language="pcm"),
        TransferGates(),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.prompt == "Which time make I schedule am?"


def test_scheduled_transfer_confirmation_copy_is_schedule_specific() -> None:
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_resolved_name="Fatima Zahra Musa",
        recipient_account="0034575515",
        recipient_bank_name="Gtb",
        source_bank_name="Access Bank",
        source_account_number="0003",
        schedule_mode="one_time",
        recurrence_type="one_time",
        schedule_start_date="2026-05-22",
        schedule_time_local="08:00",
    )

    result = build_confirmation(payload, TransferContext(phone_number="2348000000999", language="en"))

    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.confirmation_summary
    assert "Scheduled for:" in result.confirmation_summary
    assert "8:00 AM WAT" in result.confirmation_summary


def test_scheduled_transfer_confirmation_header_is_distinct() -> None:
    header = build_confirmation_header(
        task_types=["transfer"],
        task_actions=["schedule_transfer"],
        locale="en",
        task_count=1,
    )
    immediate_header = build_confirmation_header(
        task_types=["transfer"],
        task_actions=["send_money"],
        locale="en",
        task_count=1,
    )

    assert header == "Confirm Scheduled Transfer"
    assert immediate_header == "Confirm Transfer"
