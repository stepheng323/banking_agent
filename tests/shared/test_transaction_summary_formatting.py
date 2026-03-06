from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from shared.formatters.transaction_summary import format_batch_transfer_summary, format_multi_action_summary


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

    assert "✓ ₦10,000 → Mum (Mercy Johnson) • Opay • 8162511023" in summary
    assert "✓ ₦10,000 → Tolu (Tolu Adedayo) • Access • 0760505261" in summary
    assert "*Total Spent:* ₦20,000" in summary
    assert "₦20,000.00" not in summary


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
