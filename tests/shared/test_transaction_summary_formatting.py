from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from banking.presentation.formatters.batch_transfer_summary import format_batch_transfer_summary
from banking.presentation.formatters.multi_action_summary import (
    format_multi_action_summary,
    format_multi_action_summary_blocks,
)
from shared.messaging.body_blocks import render_body_blocks_text


def _transfer_task(
    *,
    task_id: str,
    amount: float,
    recipient_name: str | None,
    recipient_resolved_name: str | None,
    bank: str | None,
    account: str | None,
) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        type="transfer",
        stage=TaskStage.COMPLETED,
        payload={
            "amount": amount,
            "recipient_name": recipient_name,
            "recipient_resolved_name": recipient_resolved_name,
            "recipient_bank_name": bank,
            "recipient_account": account,
        },
    )


def _airtime_task(
    *,
    task_id: str,
    amount: float,
    recipient_phone: str | None = None,
    phone_number: str | None = None,
    network: str | None = None,
) -> TaskSpec:
    payload: dict[str, str | float] = {"amount": amount}
    if recipient_phone is not None:
        payload["recipient_phone"] = recipient_phone
    if phone_number is not None:
        payload["phone_number"] = phone_number
    if network is not None:
        payload["network"] = network
    return TaskSpec(
        id=task_id,
        type="airtime",
        stage=TaskStage.COMPLETED,
        payload=payload,
    )


def _data_task(
    *,
    task_id: str,
    amount: float,
    phone_number: str,
    plan_name: str,
) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        type="data",
        stage=TaskStage.COMPLETED,
        payload={
            "amount": amount,
            "phone_number": phone_number,
            "plan_name": plan_name,
        },
    )


def test_multi_action_summary_uses_compact_transfer_line_with_alias_and_resolved() -> None:
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=10000,
            recipient_name="Mum",
            recipient_resolved_name="MERCY JOHNSON",
            bank="Opay",
            account="8162511023",
        )
    ]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "*Transfer Complete*" in summary
    assert "✓ ₦10,000 → Mum (Mercy Johnson) • Opay • 8162511023" in summary
    assert "₦10,000.00" not in summary


def test_multi_action_summary_total_spent_is_compact_for_multiple_tasks() -> None:
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=10000,
            recipient_name="Mum",
            recipient_resolved_name="MERCY JOHNSON",
            bank="Opay",
            account="8162511023",
        ),
        _transfer_task(
            task_id="t2",
            amount=10000,
            recipient_name="Tolu",
            recipient_resolved_name="TOLU ADEDAYO",
            bank="Access",
            account="0760505261",
        ),
    ]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "*Transfers Complete*" in summary
    assert "✓ ₦10,000 → Mum (Mercy Johnson) • Opay • 8162511023" in summary
    assert "✓ ₦10,000 → Tolu (Tolu Adedayo) • Access • 0760505261" in summary
    assert "*Total Spent:* ₦20,000" in summary
    assert "₦20,000.00" not in summary


def test_multi_action_summary_blocks_space_failed_transfers_for_mobile() -> None:
    first = _transfer_task(
        task_id="t1",
        amount=30000,
        recipient_name="Mom",
        recipient_resolved_name="FATIMA ZAHRA MUSA",
        bank="Wema",
        account="8067892221",
    )
    first.payload["final_status"] = "failed"
    first.payload["error_message"] = "Transfer creation failed"
    second = _transfer_task(
        task_id="t2",
        amount=30000,
        recipient_name="Ay",
        recipient_resolved_name="EMMANUEL TUNDE BAKARE",
        bank="Opay",
        account="7750145200",
    )
    second.payload["final_status"] = "failed"
    second.payload["error_message"] = "Transfer creation failed"

    rendered = render_body_blocks_text(format_multi_action_summary_blocks([first, second], locale="en"))

    assert "Transfers failed" in rendered
    assert "✗ ₦30,000 → Mom (Fatima Zahra Musa)\nWema • 8067892221\nReason: Transfer creation failed" in rendered
    assert "\n\n✗ ₦30,000 → Ay (Emmanuel Tunde Bakare)\nOpay • 7750145200" in rendered
    assert "All 2 transactions failed." in rendered
    assert "✗ ₦30,000 → Mom (Fatima Zahra Musa) • Wema • 8067892221" not in rendered


def test_multi_action_summary_transfer_line_uses_safe_bank_and_account_fallbacks() -> None:
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=5000,
            recipient_name="Tolu",
            recipient_resolved_name=None,
            bank=None,
            account=None,
        )
    ]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "✓ ₦5,000 → Tolu • Bank • N/A" in summary


def test_multi_action_summary_resolved_only_line_is_title_cased() -> None:
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=10000,
            recipient_name=None,
            recipient_resolved_name="GRACE NGOZI ADEBAYO",
            bank="Access Bank",
            account="0762511023",
        )
    ]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "✓ ₦10,000 → Grace Ngozi Adebayo • Access Bank • 0762511023" in summary


def test_multi_action_summary_airtime_line_uses_single_checkmark() -> None:
    tasks = [_airtime_task(task_id="a1", amount=1000, phone_number="08162511023", network="MTN")]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "*Airtime Purchase Complete*" in summary
    assert "\u2713 *Airtime:*" in summary
    assert "\u2713 \u2713 *Airtime:*" not in summary
    assert "for 08162511023 (MTN)" in summary


def test_multi_action_summary_airtime_uses_recipient_phone_fallback() -> None:
    tasks = [_airtime_task(task_id="a1", amount=1000, recipient_phone="08162511023", network="MTN")]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "for 08162511023 (MTN)" in summary


def test_multi_action_summary_redacts_technical_failure_reason() -> None:
    task = _airtime_task(task_id="a1", amount=2000, recipient_phone="08162511023", network="MTN")
    task.payload.update(
        {
            "final_status": "failed",
            "error_message": (
                "Execution failed: This Session's transaction has been rolled back. "
                "[SQL: INSERT INTO transactions ...] password=secret"
            ),
        }
    )

    summary = format_multi_action_summary([task], locale="en")

    assert "Reason: Airtime purchase could not be completed. Please try again." in summary
    assert "Session's transaction" not in summary
    assert "[SQL:" not in summary
    assert "password" not in summary
    assert "secret" not in summary


def test_multi_action_summary_mixed_batch_keeps_neutral_wrapper_copy() -> None:
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=5000,
            recipient_name="Tolu",
            recipient_resolved_name="TOLU ADEDAYO",
            bank="First Bank",
            account="0760505261",
        ),
        _airtime_task(task_id="a1", amount=1000, phone_number="08162511023", network="MTN"),
    ]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "*Transaction Summary*" in summary
    assert "All 2 transactions completed successfully." in summary


def test_multi_action_summary_processing_transfer_uses_update_copy() -> None:
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=10000,
            recipient_name="Mum",
            recipient_resolved_name="MERCY JOHNSON",
            bank="Opay",
            account="8162511023",
        ),
        TaskSpec(
            id="t2",
            type="transfer",
            stage=TaskStage.COMPLETED,
            payload={
                "amount": 7000,
                "recipient_name": "Tolu",
                "recipient_resolved_name": "TOLU ADEDAYO",
                "recipient_bank_name": "First Bank",
                "recipient_account": "0760505261",
                "final_status": "processing",
            },
        ),
    ]

    summary = format_multi_action_summary(tasks, locale="en")

    assert "*Transfers Update*" in summary
    assert "✓ ₦10,000 → Mum (Mercy Johnson) • Opay • 8162511023" in summary
    assert "… ₦7,000 → Tolu (Tolu Adedayo) • First Bank • 0760505261" in summary
    assert "*Total Spent:* ₦10,000" in summary
    assert "Some transactions completed; others are still being processed." in summary


def test_multi_action_summary_processing_footer_uses_locale_catalog() -> None:
    data_task = _data_task(
        task_id="d1",
        amount=1500,
        phone_number="08162511023",
        plan_name="MTN 2GB",
    )
    data_task.payload["final_status"] = "processing"
    tasks = [
        _transfer_task(
            task_id="t1",
            amount=5000,
            recipient_name="Tolu",
            recipient_resolved_name="TOLU ADEDAYO",
            bank="First Bank",
            account="0760505261",
        ),
        data_task,
    ]

    summary = format_multi_action_summary(tasks, locale="pcm")

    assert "*Transaction Update*" in summary
    assert "Others still dey wait for provider confirmation" in summary


def test_multi_action_summary_failed_footer_uses_locale_catalog() -> None:
    task = _airtime_task(task_id="a1", amount=2000, recipient_phone="08162511023", network="MTN")
    task.payload["final_status"] = "failed"
    task.payload["error_message"] = "Provider is temporarily unavailable"

    summary = format_multi_action_summary([task], locale="ha")

    assert "Duk transactions sun kasa." in summary


def test_batch_confirmation_summary_uses_compact_total_amount() -> None:
    summary = format_batch_transfer_summary(
        num_transfers=2,
        total_amount=20000,
        source_account_info="From: Zenith Bank (···9384)",
        summaries=["₦10,000 → Mum", "₦10,000 → Tolu"],
        locale="en",
    )

    assert "Total out: ₦20,000" in summary
    assert "Total out: ₦20,000.00" not in summary
