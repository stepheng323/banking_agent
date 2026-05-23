from __future__ import annotations

import pytest

from shared.formatters.transfer import (
    format_transfer_pending_message,
    format_transfer_success_message,
    format_transfer_summary,
)
from shared.i18n import LocaleCode, render_message
from shared.i18n.personality import (
    PersonalityContext,
    render_personalized_message,
    select_tone_variant,
    transfer_personality_context_from_payload,
)
from shared.i18n.renderer import _get_by_dotted_key, _read_catalog


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


def test_personality_variant_keys_exist_in_all_supported_catalogs() -> None:
    required_keys = {
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
