from __future__ import annotations

import pytest

from shared.formatters.airtime import format_airtime_summary
from shared.formatters.data import format_data_summary
from shared.formatters.transfer_notifications import (
    format_transfer_pending_message,
    format_transfer_success_message,
)
from shared.formatters.transfer_summary import (
    format_transfer_summary,
)
from shared.i18n.models import LocaleCode
from shared.i18n.personality import (
    PersonalityContext,
    render_personalized_message,
    select_tone_variant,
    transfer_personality_context_from_payload,
)
from shared.i18n.renderer import _get_by_dotted_key, _read_catalog, render_message


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (PersonalityContext(moment="success", amount=5000, saved_recipient=True), "warm"),
        (PersonalityContext(moment="success", amount=5000, pooled_funding=True), "celebratory"),
        (PersonalityContext(moment="confirmation", amount=50000, saved_recipient=False), "careful"),
        (PersonalityContext(moment="confirmation", amount=50000, saved_recipient=True), "trusted_careful"),
        (PersonalityContext(moment="success", amount=5000, first_successful_transfer=True), "celebratory"),
        (PersonalityContext(moment="success", amount=5000, largest_successful_transfer=True), "celebratory"),
        (PersonalityContext(moment="success", amount=5000, first_pooled_success=True), "celebratory"),
        (PersonalityContext(moment="failure"), "reassuring"),
        (PersonalityContext(moment="insufficient_funds"), "reassuring"),
        (None, "standard"),
    ],
)
def test_select_tone_variant(context: PersonalityContext | None, expected: str) -> None:
    assert select_tone_variant(context) == expected


def test_transfer_personality_context_reads_existing_payload_signals() -> None:
    context = transfer_personality_context_from_payload(
        {
            "amount": "2500",
            "beneficiary_id": "ben_123",
            "funding_plan": {"is_single_source": False},
            "is_high_risk_transfer": False,
            "recipient_success_count_90d": "3",
        },
        moment="success",
    )

    assert context.amount == 2500
    assert context.saved_recipient is True
    assert context.pooled_funding is True
    assert context.recipient_success_count_90d == 3


def test_render_personalized_message_uses_selected_variant() -> None:
    message = render_personalized_message(
        "transfer.format.notifications.success",
        "en",
        {"amount": "₦5,000", "recipient_name": "Tolu Adebayo", "transaction_id": "tx_123"},
        PersonalityContext(moment="success", amount=5000, saved_recipient=True),
    )

    assert message == "Done. ₦5,000 has been sent to Tolu Adebayo."


def test_render_personalized_message_falls_back_to_standard_variant() -> None:
    message = render_personalized_message(
        "transfer.format.notifications.success",
        "en",
        {"amount": "₦50,000", "recipient_name": "Tolu Adebayo", "transaction_id": "tx_123"},
        PersonalityContext(moment="success", amount=50000, saved_recipient=False, high_risk=True),
    )

    assert message == "✓ Transfer successful! ₦50,000 has been sent to Tolu Adebayo."


def test_render_personalized_message_falls_back_to_base_key_without_variants() -> None:
    base = render_message("conversational.identity", "en")
    message = render_personalized_message(
        "conversational.identity",
        "en",
        context=PersonalityContext(moment="success", amount=1000, saved_recipient=True),
    )

    assert message == base


def test_transfer_formatters_preserve_existing_copy_without_context() -> None:
    message = format_transfer_success_message(
        amount=5000,
        recipient_name="Tolu Adebayo",
        transaction_id="mock_debit_5e23f4cd782d",
        locale="en",
    )

    assert message == "✓ Transfer successful! ₦5,000 has been sent to Tolu Adebayo."


def test_transfer_formatters_pick_expected_variants_with_context() -> None:
    warm_success = format_transfer_success_message(
        amount=5000,
        recipient_name="Tolu Adebayo",
        transaction_id="mock_debit_5e23f4cd782d",
        locale="en",
        personality_context=PersonalityContext(moment="success", amount=5000, saved_recipient=True),
    )
    celebratory_success = format_transfer_success_message(
        amount=5000,
        recipient_name="Tolu Adebayo",
        transaction_id="mock_debit_5e23f4cd782d",
        locale="en",
        personality_context=PersonalityContext(moment="success", amount=5000, pooled_funding=True),
    )
    pending = format_transfer_pending_message(
        amount=5000,
        recipient_name="Tolu Adebayo",
        locale="en",
        personality_context=PersonalityContext(moment="pending", amount=5000, saved_recipient=True),
    )
    confirmation_summary = format_transfer_summary(
        {
            "amount": 50000,
            "recipientName": "Tolu Adebayo",
            "recipientBank": "Access Bank",
            "recipientAccount": "8067892221",
        },
        include_source=False,
        locale="en",
        personality_context=PersonalityContext(moment="confirmation", amount=50000, saved_recipient=False),
    )
    trusted_confirmation_summary = format_transfer_summary(
        {
            "amount": 50000,
            "recipientName": "Tolu Adebayo",
            "recipientBank": "Access Bank",
            "recipientAccount": "8067892221",
        },
        include_source=False,
        locale="en",
        personality_context=PersonalityContext(moment="confirmation", amount=50000, saved_recipient=True),
    )

    assert warm_success == "Done. ₦5,000 has been sent to Tolu Adebayo."
    assert celebratory_success == "All set. ₦5,000 has been sent to Tolu Adebayo."
    assert pending.startswith("Got it. Your ₦5,000 transfer to Tolu Adebayo is processing.")
    assert confirmation_summary.splitlines()[0] == "*Review carefully: ₦50,000 to Tolu Adebayo*"
    assert trusted_confirmation_summary.splitlines()[0] == "*Ready, please review: ₦50,000 to Tolu Adebayo*"


def test_airtime_and_data_formatters_pick_expected_variants_with_context() -> None:
    airtime_summary = format_airtime_summary(
        {
            "amount": 1000,
            "recipientPhone": "08162511023",
            "network": "MTN",
            "sourceBank": "Kuda",
            "sourceAccount": "0000000001",
        },
        locale="en",
        personality_context=PersonalityContext(moment="confirmation", amount=1000, saved_recipient=True),
    )
    data_summary = format_data_summary(
        {
            "planName": "MTN 2GB",
            "amount": 1500,
            "recipientPhone": "08162511023",
            "network": "MTN",
            "sourceBank": "Kuda",
            "sourceAccount": "0000000001",
        },
        locale="en",
        personality_context=PersonalityContext(moment="confirmation", amount=1500, saved_recipient=True),
    )

    assert airtime_summary.splitlines()[0] == "*₦1,000 airtime for 08162511023*"
    assert data_summary.splitlines()[0] == "*MTN 2GB for 08162511023*"


def test_airtime_and_data_notification_variants_render() -> None:
    airtime_success = render_personalized_message(
        "airtime.executor.success_message",
        "en",
        {
            "amount": "1,000.00",
            "recipient_phone": "08162511023",
            "recipient_target": "Tolu (08162511023)",
            "network": "MTN",
            "reference": "ref-1",
        },
        PersonalityContext(moment="success", amount=1000, saved_recipient=True),
    )
    data_pending = render_personalized_message(
        "data.completion.pending_message",
        "en",
        {
            "plan_name": "MTN 2GB",
            "amount": "1,500.00",
            "recipient_phone": "08162511023",
        },
        PersonalityContext(moment="pending", amount=1500, saved_recipient=True),
    )
    data_failure = render_personalized_message(
        "data.completion.failed_message",
        "en",
        {"error_message": "Provider unavailable"},
        PersonalityContext(moment="failure", amount=1500),
    )

    assert airtime_success == "Done. ₦1,000.00 airtime has been sent to Tolu (08162511023) (MTN).\nRef: ref-1"
    assert data_pending.startswith("Got it. Your MTN 2GB purchase (₦1,500.00)")
    assert data_failure == "I couldn't complete the data purchase: Provider unavailable. Please try again."


@pytest.mark.parametrize(
    ("locale", "airtime_marker", "data_pending_marker", "data_failure_marker"),
    [
        ("pcm", "don reach", "I don get am", "I no fit complete"),
        ("yo", "ti lọ si", "Mo ti gba a", "Mi o le pari"),
        ("ha", "ya tafi zuwa", "Na karba", "Ban iya kammala"),
        ("ig", "erutela", "Enwetara m ya", "Enweghị m ike"),
    ],
)
def test_airtime_and_data_personality_variants_are_localized(
    locale: str,
    airtime_marker: str,
    data_pending_marker: str,
    data_failure_marker: str,
) -> None:
    airtime_success = render_personalized_message(
        "airtime.executor.success_message",
        locale,
        {
            "amount": "1,000.00",
            "recipient_phone": "08162511023",
            "recipient_target": "Tolu (08162511023)",
            "network": "MTN",
            "reference": "ref-1",
        },
        PersonalityContext(moment="success", amount=1000, saved_recipient=True),
    )
    data_pending = render_personalized_message(
        "data.completion.pending_message",
        locale,
        {
            "plan_name": "MTN 2GB",
            "amount": "1,500.00",
            "recipient_phone": "08162511023",
        },
        PersonalityContext(moment="pending", amount=1500, saved_recipient=True),
    )
    data_failure = render_personalized_message(
        "data.completion.failed_message",
        locale,
        {"error_message": "Provider unavailable"},
        PersonalityContext(moment="failure", amount=1500),
    )

    assert airtime_marker in airtime_success
    assert data_pending_marker in data_pending
    assert data_failure_marker in data_failure


def test_personality_variant_keys_exist_in_all_supported_catalogs() -> None:
    required_keys = {
        "airtime.execution.message_queued_variants.standard",
        "airtime.execution.message_queued_variants.warm",
        "airtime.execution.message_queued_variants.reassuring",
        "airtime.executor.failure_message_variants.standard",
        "airtime.executor.failure_message_variants.reassuring",
        "airtime.executor.success_message_variants.standard",
        "airtime.executor.success_message_variants.warm",
        "airtime.executor.success_message_variants.celebratory",
        "airtime.format.summary.title_variants.standard",
        "airtime.format.summary.title_variants.warm",
        "airtime.format.summary.title_variants.careful",
        "airtime.format.summary.title_variants.trusted_careful",
        "data.completion.failed_message_variants.standard",
        "data.completion.failed_message_variants.reassuring",
        "data.completion.pending_message_variants.standard",
        "data.completion.pending_message_variants.warm",
        "data.completion.pending_message_variants.reassuring",
        "data.completion.success_message_variants.standard",
        "data.completion.success_message_variants.warm",
        "data.completion.success_message_variants.celebratory",
        "data.format.summary.title_variants.standard",
        "data.format.summary.title_variants.warm",
        "data.format.summary.title_variants.careful",
        "data.format.summary.title_variants.trusted_careful",
        "transaction_copy.confirmation.transfer_variants.standard",
        "transaction_copy.confirmation.transfer_variants.warm",
        "transaction_copy.confirmation.transfer_variants.careful",
        "transaction_copy.confirmation.transfer_variants.trusted_careful",
        "transfer.confirmation.high_risk_unsaved_warning_variants.standard",
        "transfer.confirmation.high_risk_unsaved_warning_variants.careful",
        "transfer.funding.insufficient_funds_variants.standard",
        "transfer.funding.insufficient_funds_variants.reassuring",
        "transfer.execution.failed_variants.standard",
        "transfer.execution.failed_variants.reassuring",
        "transfer.error.execution_failed_variants.standard",
        "transfer.error.execution_failed_variants.reassuring",
        "transfer.error.provider_failed_variants.standard",
        "transfer.error.provider_failed_variants.reassuring",
        "transfer.format.summary.title_variants.standard",
        "transfer.format.summary.title_variants.warm",
        "transfer.format.summary.title_variants.careful",
        "transfer.format.summary.title_variants.trusted_careful",
        "transfer.format.notifications.success_variants.standard",
        "transfer.format.notifications.success_variants.warm",
        "transfer.format.notifications.success_variants.celebratory",
        "transfer.format.notifications.pending_variants.standard",
        "transfer.format.notifications.pending_variants.warm",
        "transfer.format.notifications.pending_variants.reassuring",
    }

    for locale in LocaleCode:
        catalog = _read_catalog(locale)
        missing = [key for key in required_keys if _get_by_dotted_key(catalog, key) is None]
        assert missing == []
