from apps.core.src.agent.orchestrator.utils.task_payload import build_task_spec_from_plan_item
from shared.types.planner import PlannedTask, TaskParameters


def test_transfer_recipient_not_in_user_text_is_dropped() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to tolu",
        parameters=TaskParameters(amount=5000, recipient="Tolu Adebayo"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to tolu",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "transfer"
    assert spec.payload.get("recipient_name") == "tolu"
    assert spec.payload.get("skip_extraction") is True


def test_transfer_recipient_in_user_text_is_preserved() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to Tolu Adebayo",
        parameters=TaskParameters(amount=5000, recipient="Tolu Adebayo"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to tolu adebayo",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "Tolu Adebayo"
    assert spec.payload.get("skip_extraction") is True


def test_airtime_still_uses_skip_extraction() -> None:
    plan_item = PlannedTask(
        task_id="a1",
        action="buy_airtime",
        executor="airtime",
        instruction="Buy airtime",
        parameters=TaskParameters(amount=1000, recipient_phone="08030000000"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy airtime",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "airtime"
    assert spec.payload.get("skip_extraction") is True


def test_transfer_maps_planner_bank_name_and_normalizes_recipient_account() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Please send 5k to 816 251 1023 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="816 251 1023 Access",
            recipient_account="816 251 1023",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Please send 5k to 816 251 1023 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "transfer"
    assert spec.payload.get("amount") == 5000
    assert spec.payload.get("recipient_account") == "8162511023"
    assert spec.payload.get("recipient_bank_name") == "Access Bank"


def test_transfer_keeps_10_digit_account_starting_with_zero() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to 0760505261 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="0760505261 Access",
            recipient_account="0760505261",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 5k to 0760505261 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_account") == "0760505261"


def test_transfer_trims_leading_zero_for_11_digit_account() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to 08162511023 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="08162511023 Access",
            recipient_account="08162511023",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 5k to 08162511023 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_account") == "8162511023"


def test_transfer_keeps_11_digit_account_without_leading_zero() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to 18162511023 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="18162511023 Access",
            recipient_account="18162511023",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 5k to 18162511023 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_account") == "18162511023"
