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
