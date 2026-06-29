from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from banking.presentation.formatters.query_transaction_copy import (
    build_transaction_detail_lines,
    format_transaction_evidence_line,
    format_transaction_list_item,
    format_transaction_status_reply,
)
from banking.presentation.formatters.support_transaction_copy import format_support_transfer_status_sentence
from banking.presentation.formatters.transaction_confirmation_copy import (
    build_confirmation_header,
    format_confirmation_section,
)
from banking.presentation.i18n.personality import PersonalityContext


def test_format_transaction_status_reply_handles_aliases() -> None:
    assert format_transaction_status_reply("confirmed", locale="en") == "That transaction is successful."
    assert format_transaction_status_reply("in_progress", locale="en") == "That transaction is processing."
    assert format_transaction_status_reply("declined", locale="en") == "That transaction failed."


def test_format_transaction_status_reply_handles_unified_processing_bank_posted() -> None:
    message = format_transaction_status_reply(
        "processing",
        locale="en",
        local_status="processing",
        bank_status="posted",
    )

    assert (
        message
        == "That transaction is still processing in our app, but a matching debit is posted in your bank history."
    )


def test_format_transaction_status_reply_handles_unified_failed_bank_posted_review() -> None:
    message = format_transaction_status_reply(
        "failed",
        locale="en",
        local_status="failed",
        bank_status="posted",
        needs_review=True,
    )

    assert (
        message
        == "Our app record says that transaction failed, but a matching debit is posted in your bank history. Please ask support to review it."
    )


def test_format_transaction_status_reply_localizes_status_copy() -> None:
    assert format_transaction_status_reply("posted", locale="pcm") == "That transaction don post."


def test_format_transaction_status_reply_handles_missing_status() -> None:
    assert (
        format_transaction_status_reply(None, locale="en")
        == "I found the transaction, but I couldn't confirm the status."
    )


def test_format_transaction_list_item_uses_query_row_copy() -> None:
    item = SimpleNamespace(
        amount=10000,
        description="Transfer to Tolu",
        date=date(2026, 6, 11),
        metadata={"type": "debit", "counterparty": "Tolu Adebayo", "bank_name": "GTBank"},
    )

    assert format_transaction_list_item(item, locale="en") == "• Jun 11 · ₦10,000 — Sent to Tolu Adebayo · GTBank"


def test_format_transaction_list_item_humanizes_uppercase_counterparty() -> None:
    item = SimpleNamespace(
        amount=5000,
        description="Transfer",
        date=date(2026, 6, 25),
        metadata={"type": "debit", "transaction_type": "transfer", "counterparty": "FATIMA ZAHRA MUSA"},
    )

    assert format_transaction_list_item(item, locale="en") == "• Jun 25 · ₦5,000 — Sent to Fatima Zahra Musa"


def test_format_transaction_list_item_marks_failed_transfer_without_sent_label() -> None:
    item = SimpleNamespace(
        amount=50000,
        description="Transfer to Tolu",
        date=date(2026, 6, 11),
        metadata={
            "type": "debit",
            "transaction_type": "transfer",
            "counterparty": "Tolu Adebayo",
            "bank_name": "GTBank",
            "display_status": "failed",
        },
    )

    assert format_transaction_list_item(item, locale="en") == "• Failed · Jun 11 · ₦50,000 — Transfer to Tolu Adebayo · GTBank"


def test_format_transaction_list_item_marks_reversed_transfer_without_sent_label() -> None:
    item = SimpleNamespace(
        amount=3000,
        description="Transfer to Tolu",
        date=date(2026, 6, 11),
        metadata={
            "type": "debit",
            "transaction_type": "transfer",
            "counterparty": "Tolu Adebayo",
            "bank_name": "GTBank",
            "status": "reversed",
        },
    )

    assert format_transaction_list_item(item, locale="en") == "• Reversed · Jun 11 · ₦3,000 — Transfer to Tolu Adebayo · GTBank"


def test_build_transaction_detail_lines_uses_query_field_copy() -> None:
    item = SimpleNamespace(
        id="tx-ref-1",
        amount=10000,
        description="Transfer to Tolu",
        date=date(2026, 5, 17),
        metadata={
            "type": "debit",
            "bank_name": "GTBank",
            "transaction_type": "transfer",
            "status": "successful",
            "transaction_id": "tx-ref-1",
        },
    )

    assert build_transaction_detail_lines(item, locale="en") == [
        "*Amount:* ₦10,000.00",
        "*Description:* Transfer to Tolu",
        "*Date:* May 17, 2026",
        "*Type:* Outgoing (Debit)",
        "*Bank:* GTBank",
        "*Category:* Transfer",
        "*Status:* ✓ Successful",
        "*Ref:* tx-ref-1",
    ]


def test_format_transaction_evidence_line_respects_used_fields() -> None:
    assert (
        format_transaction_evidence_line(
            amount=950000,
            date_value=date(2026, 5, 13),
            counterparty="Acme Corp",
            bank_name="Kuda",
            used_fields={"bank"},
            locale="en",
        )
        == "₦950,000 • May 13 • Acme Corp"
    )


def test_format_support_transfer_status_sentence_uses_support_copy() -> None:
    message = format_support_transfer_status_sentence(
        {
            "amount": 10000,
            "recipient_name": "Tolu Adebayo",
            "created_at": "2026-05-17T06:49:00",
        },
        status="successful",
        locale="en",
    )

    assert message == "• This transfer of ₦10,000 to Tolu Adebayo was successful on May 17 at 06:49 AM."


def test_format_confirmation_section_labels_mixed_task_summary() -> None:
    assert (
        format_confirmation_section(
            task_type="data",
            summary="MTN 5 GB data bundle for your number\nNetwork: MTN • Amount: ₦3,500",
            locale="en",
        )
        == "*Data*\nMTN 5 GB data bundle for your number\nNetwork: MTN • Amount: ₦3,500"
    )


def test_build_confirmation_header_uses_review_copy_for_mobile_transactions() -> None:
    context = PersonalityContext(moment="confirmation", amount=3500, saved_recipient=True)

    assert (
        build_confirmation_header(
            task_types=["airtime"],
            locale="en",
            task_count=1,
            personality_context=context,
        )
        == "Review Airtime Purchase"
    )
    assert (
        build_confirmation_header(
            task_types=["data"],
            locale="en",
            task_count=1,
            personality_context=context,
        )
        == "Review Data Purchase"
    )


def test_build_confirmation_header_keeps_scheduled_mobile_copy_specific() -> None:
    context = PersonalityContext(moment="confirmation", amount=3500, saved_recipient=True)

    assert (
        build_confirmation_header(
            task_types=["airtime"],
            locale="en",
            task_count=1,
            task_actions=["schedule_airtime"],
            personality_context=context,
        )
        == "Confirm Scheduled Airtime Purchase"
    )
    assert (
        build_confirmation_header(
            task_types=["data"],
            locale="en",
            task_count=1,
            task_actions=["schedule_data"],
            personality_context=context,
        )
        == "Confirm Scheduled Data Purchase"
    )
